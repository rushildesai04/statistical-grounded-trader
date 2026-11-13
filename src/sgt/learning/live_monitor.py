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

import csv
import logging
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

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


@dataclass(frozen=True, slots=True)
class ForwardBook:
    """A forward research record judged like a book: every channel alert scored by the frozen rule on SIP bars, so it
    sees the edge on far more trades than the live book (2026-11-12 night: the live no-news book had 7 trades while the
    forward record had 61 at -3.44%/trade; its win-rate test would have alarmed on 11-10). An alarm cuts ``scales``."""
    name: str
    rel_path: str                    # relative to the reports dir that holds ops/ops.db
    column: str
    expectancy: float
    std: float
    win_rate: float
    scales: str                      # the live book whose new-trade size an alarm halves
    source: str


FORWARD = ForwardBook("fade forward record (every alert)", "fade_challengers/shadow.csv", "net_champion",
                      0.0112, 0.1765, 0.573, "alert fade (no-news)",
                      "prereg_alert_fade_2026-11-01 held-out, 764 trades (reports/alert_fade/trades.csv)")
MAX_FORWARD_BYTES = 5_000_000        # the record is ~100 bytes/trade: far above years of alerts, bounds a bad file


def forward_path(db: OpsStore, b: ForwardBook = FORWARD) -> Path:
    """reports/ops/ops.db -> reports/<rel_path>. Written nightly at 21:20 (fadechallengers), so the 20:50 digest
    judges the record through the previous session."""
    return Path(db.path).resolve().parent.parent / b.rel_path


def _judge(out: dict, nets: list[float], expectancy: float, std: float, win_rate: float) -> dict:
    out["n"] = len(nets)
    if not nets:
        return out
    cusum, wins = CusumMonitor(expectancy, std), WinRateMonitor(win_rate)
    for x in nets:
        for mon in (cusum, wins):
            sig = mon.update(x)
            if sig is not None and sig.action in (Action.DEMOTE, Action.HALT, Action.DISABLE):
                out["alarms"].append(sig.detail)
    out["mean"], out["half_width"] = _ci(nets)
    out["win"] = sum(x > 0 for x in nets) / len(nets)
    return out


def check_trade_book(db: OpsStore, b: TradeBook) -> dict:
    with db.tx() as c:
        nets = [float(r[0]) for r in c.execute(b.sql) if r[0] is not None]
    return _judge({"book": b.name, "expected": b.expectancy, "alarms": []}, nets, b.expectancy, b.std, b.win_rate)


def check_forward(db: OpsStore, b: ForwardBook = FORWARD) -> dict:
    """No file yet -> n 0 (no line). A file that is too large or unreadable -> ``error`` (a NEEDS YOU line, no size
    cut: a broken file is not evidence about the edge). Non-finite values are dropped."""
    out = {"book": b.name, "n": 0, "expected": b.expectancy, "alarms": [], "scales": b.scales,
           "unit": "forward trades"}
    p = forward_path(db, b)
    if not p.exists():
        return out
    try:
        if p.stat().st_size > MAX_FORWARD_BYTES:
            raise ValueError(f"over {MAX_FORWARD_BYTES:,} bytes")
        with p.open(newline="") as fh:
            nets = [float(r[b.column]) for r in csv.DictReader(fh)]
    except (OSError, KeyError, ValueError, TypeError, csv.Error) as e:
        log.error("forward record %s unreadable: %s", p, e)
        out["error"] = f"forward record unreadable ({type(e).__name__})"
        return out
    return _judge(out, [x for x in nets if math.isfinite(x)], b.expectancy, b.std, b.win_rate)


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
    return ([check_trade_book(db, b) for b in BOOKS] + [check_forward(db)]
            + [check_model_book(db, s) for s in MODEL_BOOKS])


def line(r: dict) -> str | None:
    """One digest line for a book with data (or an unreadable record), '!' first when a monitor tripped."""
    if r.get("error"):
        return f"! {r['book']}: {r['error']}"
    if not r["n"]:
        return None
    if "expected" in r:
        body = (f"{r['book']}: {r['n']} {r.get('unit', 'live trades')}, {r['mean']:+.2%}/trade"
                + (f" (±{r['half_width']:.1%})" if not math.isnan(r["half_width"]) else "")
                + f", win {r['win']:.0%}; tested {r['expected']:+.2%}")
    else:
        body = f"{r['book']}: {r['n']} days, {r['cum']:+.1%} total, drawdown {r['drawdown']:+.1%}"
    return ("! DRIFT " if r["alarms"] else "") + body + (f" -> {r['alarms'][-1]}" if r["alarms"] else "")


def lines(results: list[dict]) -> list[str]:
    """Digest lines: one per book with data, '!' first when a monitor tripped."""
    return [ln for r in results if (ln := line(r)) is not None]


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


def _new_alarms(db: OpsStore, results: list[dict], today: date) -> list[tuple[str | None, str]]:
    """(book whose size was cut or None, text) for alarms not yet texted (one per book per alarm detail). A new alarm
    on a book whose target (``scales``, else itself) is in AUTO_BOOKS halves that target's size for new trades (floor
    MIN_SCALE) and says so in the text."""
    out = []
    for r in results:
        for a in r["alarms"][-1:]:
            key = f"drift:{r['book']}:{a[:80]}"
            if db.flag(key) is None:
                db.set_flag(key, today.isoformat())
                msg, target = f"{r['book']}: {a}", r.get("scales", r["book"])
                if target in AUTO_BOOKS:
                    was = scale(db, target)
                    now = max(MIN_SCALE, was * 0.5)
                    db.set_flag(SCALE_FLAG + target, f"{now:g}")
                    whose = "" if target == r["book"] else f" of {target}"
                    msg += (f" -> results are below the tested expectation: size for new trades{whose} cut to "
                            f"{now:.0%} (was {was:.0%}). Ask Claude to restore it.")
                    out.append((target, msg))
                else:
                    out.append((None, msg))
    return out


def new_alarms(db: OpsStore, results: list[dict], today: date) -> list[str]:
    """Texts of the alarms not yet texted (see _new_alarms)."""
    return [m for _, m in _new_alarms(db, results, today)]


def guard_lines(db: OpsStore, results: list[dict], today: date) -> list[str]:
    """Digest NEEDS YOU lines (review 2026-11-12 F-1): tonight's new alarms with their size cut, plus a standing line
    for every live book still below full size, so a cut is never silent."""
    new = [(t, m) for t, m in _new_alarms(db, results, today) if t in AUTO_BOOKS]
    out = [m for _, m in new]
    for book in AUTO_BOOKS:
        k = scale(db, book)
        if k < 1 and not any(t == book for t, _ in new):
            out.append(f"{book}: drift guard holds new trades at {k:.0%} of size (ask Claude to restore)")
    return out
