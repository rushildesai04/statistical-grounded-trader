"""Live drift monitor per paper book (learning-loop audit 2026-11-09, F1): compare each book's settled live results
with what its pre-registered test said to expect, every night, and say so in the digest.

Reuses the tested monitors in sgt.learning.decay (one-sided CUSUM vs validated expectancy, binomial win-rate test).
It never trades: an alarm is a line in the digest and a text, and the owner decides. Expectations (per trade, after
costs) come from the tests named below; model books are judged against their own pre-registered stop rule.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from sgt.learning.decay import Action, CusumMonitor, WinRateMonitor
from sgt.ops.store import OpsStore


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


def new_alarms(db: OpsStore, results: list[dict], today: date) -> list[str]:
    """Alarms not yet texted (one text per book per alarm detail)."""
    out = []
    for r in results:
        for a in r["alarms"][-1:]:
            key = f"drift:{r['book']}:{a[:80]}"
            if db.flag(key) is None:
                db.set_flag(key, today.isoformat())
                out.append(f"{r['book']}: {a}")
    return out
