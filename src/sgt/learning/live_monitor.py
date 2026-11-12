"""Live drift monitor per paper book (learning-loop audit 2026-11-09, F1): compare each book's settled live results
with what its pre-registered test said to expect, every night, and say so in the digest.

Reuses the tested monitors in sgt.learning.decay (one-sided CUSUM vs validated expectancy, binomial win-rate test).
Expectations (per trade, after costs) come from the tests named below; model books are judged against their own
pre-registered stop rule.

2026-11-12 (owner: "the learning portion NEEDS to kick in ... don't make it worse"): an alarm on a book in AUTO_BOOKS
now also halves that book's size for new trades (flag ``drift_scale:<book>``, floor MIN_SCALE). It only ever lowers
size, never trades, never closes a position, and is restored only on the owner's word. The monitors are the existing
one-sided CUSUM (k 0.5, h 5 standard deviations) and the binomial win-rate test (p < 0.01 after 20 trades).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date

from sgt.learning.decay import Action, CusumMonitor, WinRateMonitor
from sgt.ops.store import OpsStore

log = logging.getLogger(__name__)
SCALE_FLAG, MIN_SCALE = "drift_scale:", 0.25


@dataclass(frozen=True, slots=True)
class TradeBook:
    name: str
    sql: str                         # ordered per-trade official nets
    expectancy: float                # validated mean net per trade
    std: float
    win_rate: float
    source: str                      # where the expectation comes from


BOOKS = (
    # F6 live rule (no-news alerts only), from the first session it ran; real SIP stop fills, training no-news fades.
    TradeBook("alert fade (no-news)", "SELECT net FROM fade_trades WHERE status='settled' AND session>='2026-11-10' "
              "ORDER BY session, id", 0.0170, 0.1735, 0.578, "prereg_fade_stop_slippage_2026-11-09"),
    # Live paper long book under the adopted A2 rules (risk size + red-fill-candle exit; long_book.RULES). Per-trade
    # expectation = the E exit on the 2,242 training trades of prereg_long_risk_2026-11-12 (R changes size, not the
    # per-trade return). Expected NEGATIVE: the monitor alarms if live is even worse. Before A2 (2026-11-10 option A,
    # first-fill rule -2.29%): those trades are not judged against the new rule.
    TradeBook("long book (paper)", "SELECT net FROM long_trades WHERE status='settled' AND rules='A2' "
              "ORDER BY session, id", -0.0209, 0.0626, 0.137, "prereg_long_risk_2026-11-12 (A2, E per trade)"),
    # Scanner experiment: the self-spike rule's training result (it failed: expected NEGATIVE).
    TradeBook("scanner (experiment)", "SELECT net FROM scan_trades WHERE status='settled' ORDER BY session, id",
              -0.0118, 0.1201, 0.532, "prereg_self_fade_2026-11-06"),
)
MODEL_BOOKS = ("megacap", "megacap_overlay", "megacap_ext")
MODEL_STOP = -0.05                   # prereg_megacap_forward: cumulative net below -5% -> stop rule


def _ci(nets: list[float]) -> tuple[float, float]:
    n = len(nets)
    m = sum(nets) / n
    if n < 2:
        return m, float("nan")
    sd = math.sqrt(sum((x - m) ** 2 for x in nets) / (n - 1))
    return m, 1.96 * sd / math.sqrt(n)


def check_trade_book(db: OpsStore, b: TradeBook) -> dict:
    with db.tx() as c:
        nets = [float(r[0]) for r in c.execute(b.sql) if r[0] is not None]
    out = {"book": b.name, "n": len(nets), "expected": b.expectancy, "alarms": []}
    if not nets:
        return out
    cusum, wins = CusumMonitor(b.expectancy, b.std), WinRateMonitor(b.win_rate)
    for x in nets:
        for mon in (cusum, wins):
            sig = mon.update(x)
            if sig is not None and sig.action in (Action.DEMOTE, Action.HALT, Action.DISABLE):
                out["alarms"].append(sig.detail)
    out["mean"], out["half_width"] = _ci(nets)
    out["win"] = sum(x > 0 for x in nets) / len(nets)
    return out


def check_model_book(db: OpsStore, strategy: str) -> dict:
    with db.tx() as c:
        rows = c.execute("SELECT session, nav, ret FROM book_nav WHERE strategy=? ORDER BY session", (strategy,)).fetchall()
    out = {"book": strategy, "n": len(rows), "alarms": []}
    if not rows:
        return out
    navs = [r["nav"] for r in rows]
    cum = navs[-1] / 100_000.0 - 1
    peak = max(navs)
    out.update(cum=cum, drawdown=navs[-1] / peak - 1, last=rows[-1]["session"])
    if cum < MODEL_STOP:
        out["alarms"].append(f"cumulative {cum:+.1%} below the pre-registered stop {MODEL_STOP:.0%}")
    return out


def check_all(db: OpsStore) -> list[dict]:
    return [check_trade_book(db, b) for b in BOOKS] + [check_model_book(db, s) for s in MODEL_BOOKS]


def lines(results: list[dict]) -> list[str]:
    """Digest lines: one per book with data, '!' first when a monitor tripped."""
    out = []
    for r in results:
        if not r["n"]:
            continue
        if "expected" in r:
            body = (f"{r['book']}: {r['n']} live trades, {r['mean']:+.2%}/trade"
                    + (f" (±{r['half_width']:.1%})" if not math.isnan(r["half_width"]) else "")
                    + f", win {r['win']:.0%}; tested {r['expected']:+.2%}")
        else:
            body = f"{r['book']}: {r['n']} days, {r['cum']:+.1%} total, drawdown {r['drawdown']:+.1%}"
        out.append(("! DRIFT " if r["alarms"] else "") + body + (f" -> {r['alarms'][-1]}" if r["alarms"] else ""))
    return out


AUTO_BOOKS = ("alert fade (no-news)", "long book (paper)")   # the live books whose size the alarm halves


def scale(db: OpsStore, book: str) -> float:
    """Size multiplier for new trades of ``book``: 1.0 until an alarm halves it. A flag that is not a number in
    [0, 1] never raises size: it reads as MIN_SCALE (fail closed, logged)."""
    raw = db.flag(SCALE_FLAG + book)
    if raw is None:
        return 1.0
    try:
        v = float(raw)
    except ValueError:
        v = math.nan
    if not (math.isfinite(v) and 0.0 <= v <= 1.0):
        log.error("drift scale flag for %s is %r: using %.2f", book, str(raw)[:20], MIN_SCALE)
        return MIN_SCALE
    return v


def new_alarms(db: OpsStore, results: list[dict], today: date) -> list[str]:
    """Alarms not yet texted (one text per book per alarm detail). A new alarm on an AUTO_BOOKS book halves its size
    for new trades (floor MIN_SCALE) and says so in the text."""
    out = []
    for r in results:
        for a in r["alarms"][-1:]:
            key = f"drift:{r['book']}:{a[:80]}"
            if db.flag(key) is None:
                db.set_flag(key, today.isoformat())
                msg = f"{r['book']}: {a}"
                if r["book"] in AUTO_BOOKS:
                    was = scale(db, r["book"])
                    now = max(MIN_SCALE, was * 0.5)
                    db.set_flag(SCALE_FLAG + r["book"], f"{now:g}")
                    msg += (f" -> live results are below the tested expectation: size for new trades cut to "
                            f"{now:.0%} (was {was:.0%}). Ask Claude to restore it.")
                out.append(msg)
    return out


def guard_lines(db: OpsStore, results: list[dict], today: date) -> list[str]:
    """Digest NEEDS YOU lines (review 2026-11-12 F-1): tonight's new alarms with their size cut, plus a standing line
    for every live book still below full size, so a cut is never silent."""
    new = [m for m in new_alarms(db, results, today) if m.split(":", 1)[0] in AUTO_BOOKS]
    out = list(new)
    for book in AUTO_BOOKS:
        k = scale(db, book)
        if k < 1 and not any(m.startswith(book + ":") for m in new):
            out.append(f"{book}: drift guard holds new trades at {k:.0%} of size (ask Claude to restore)")
    return out
