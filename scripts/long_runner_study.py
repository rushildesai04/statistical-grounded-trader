"""Let half of a winning long run (owner's GEVO note; docs/findings/prereg_long_runner_2026-11-11.md).

    nice -n 10 .venv/bin/python scripts/long_runner_study.py      # run ONCE (frozen prereg)

The live long book on past alerts (first long setup to fill per event, the tested simulator), each trade exited the
live way (BASE: stop, 2R target, 30 minutes) and the owner's way (R1: half at the target, the rest trails the latest
swing low until the close). Report only; nothing here changes a live rule.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_family_study import Bars, fade_events, halt_events  # noqa: E402
from long_setups_study import D_STAR, latest  # noqa: E402

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.fast import long_book  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402

OUT = Path("reports/long_runner")
SEED, N_BOOT, ALPHA, MIN_TARGETS = 20261111, 10_000, 0.05, 100
K = pt.PIVOT_K
CAP60 = pd.Timedelta(minutes=60)


def first_fill(f: pd.DataFrame, t0: pd.Timestamp, halt: bool, cut: pd.Timestamp):
    """The live book's trade for one event: the first long setup to fill (long_book.first_fill on a known start)."""
    found = pt.first_red(f, t0, "L7") if halt else pt.first_red(f, t0) + pt.bull_flag_v2(f, t0) + pt.bos_long(f, t0)
    fills = []
    for s in found:
        if s.side != "long":
            continue
        t = pt.simulate(f, s, latest=cut)
        if t is not None:
            fills.append((t.entry_ts, long_book.LONG_KINDS.index(s.kind), s, t))
    if not fills:
        return None
    _, _, s, t = min(fills, key=lambda x: (x[0], x[1]))
    return s, t


def swing_lows(lo: np.ndarray) -> np.ndarray:
    """Indices of swing lows: low below the K bars before and the K bars after (patterns._pivots)."""
    return np.array(pt._pivots(lo, lo, K)[1], dtype=int)


def trail_at(j: int, entry_i: int, piv: np.ndarray, lo: np.ndarray, trail: float) -> float:
    """Trail known before bar j trades: the latest swing low at or after the entry bar confirmed by bar j-1's close."""
    ok = piv[(piv >= entry_i) & (piv + K <= j - 1)]
    return max(trail, float(lo[ok[-1]])) if len(ok) else trail


def _ns(x) -> np.datetime64:
    return pd.Timestamp(x).tz_convert("UTC").tz_localize(None).to_datetime64()


def runner(f: pd.DataFrame, s: pt.Setup, entry_ts: pd.Timestamp, start_ts: pd.Timestamp,
           cap: pd.Timedelta | None = None, at_target: bool = True) -> tuple[float, pd.Timestamp, str]:
    """Sell price of a runner that starts trailing on the bar ``start_ts`` (the target bar for R1; the bar after the
    fill bar for TRAIL). Sells when a bar trades at or below the trail (at min(trail, open); on the target bar at the
    trail, conservative), else at the close of the session's last bar (or the last bar within ``cap`` of the entry)."""
    ts = pd.to_datetime(f.ts, utc=True).dt.tz_localize(None).to_numpy("datetime64[ns]")
    lo, op, cl = f.low.to_numpy(float), f.open.to_numpy(float), f.close.to_numpy(float)
    entry_i = int(np.searchsorted(ts, _ns(entry_ts)))
    start_i = int(np.searchsorted(ts, _ns(start_ts)))
    end = len(f) if cap is None else int(np.searchsorted(ts, _ns(pd.Timestamp(entry_ts) + cap)))
    piv = swing_lows(lo)
    trail = float(s.stop)
    for j in range(start_i, max(end, start_i + 1)):
        trail = trail_at(j, entry_i, piv, lo, trail)
        if lo[j] <= trail:
            px = trail if (j == start_i and at_target) else min(trail, op[j])
            return px, pd.Timestamp(ts[j], tz="UTC"), "trail"
    last = max(end, start_i + 1) - 1
    return float(cl[last]), pd.Timestamp(ts[last], tz="UTC"), "close" if cap is None else "time"


def trail_from_entry(f: pd.DataFrame, s: pt.Setup, t: pt.Trade) -> float:
    """TRAIL gross: no target, no timer. The fill bar keeps the simulator's same-bar stop rule."""
    first = f[f.ts == t.entry_ts]
    if len(first) and float(first.low.iloc[0]) <= s.stop:
        px = s.stop if s.trigger is not None else min(s.stop, float(first.open.iloc[0]))
        return px / t.entry - 1
    nxt = f[f.ts > t.entry_ts]
    if nxt.empty:
        return float(first.close.iloc[0]) / t.entry - 1
    px, _, _ = runner(f, s, t.entry_ts, nxt.ts.iloc[0], at_target=False)
    return px / t.entry - 1


def trade_rows(ev: pd.DataFrame, bars: Bars, halt: bool) -> list[dict]:
    rows = []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        hit = first_fill(f, pd.Timestamp(r.t0), halt, latest(r.session))
        if hit is None:
            continue
        s, t = hit
        cost = float(r.cost_rt)
        row = {"symbol": r.symbol, "session": r.session, "kind": s.kind, "halt": halt, "entry_ts": t.entry_ts,
               "entry": t.entry, "stop": s.stop, "base_reason": t.reason, "base": t.gross - cost,
               "trail": trail_from_entry(f, s, t) - cost}
        if t.reason == "target":
            px, at, why = runner(f, s, t.entry_ts, t.exit_ts)
            px60, _, _ = runner(f, s, t.entry_ts, t.exit_ts, CAP60)
            run, run60 = px / t.entry - 1, px60 / t.entry - 1
            row.update(runner_exit=px, runner_at=at, runner_why=why, r_all=run - cost,
                       r1=0.5 * t.gross + 0.5 * run - cost, r1_60=0.5 * t.gross + 0.5 * run60 - cost)
        else:
            row.update(r_all=row["base"], r1=row["base"], r1_60=row["base"])
        rows.append(row)
    return rows


def boot(d: pd.DataFrame, col: str) -> np.ndarray:
    """Session-clustered bootstrap of the per-trade mean of ``col``."""
    u, inv = np.unique(d.session.to_numpy(), return_inverse=True)
    s = np.bincount(inv, d[col].to_numpy(float), len(u))
    n = np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    return s[b].sum(1) / n[b].sum(1)


def summary(d: pd.DataFrame) -> dict:
    d = d.assign(diff=d.r1 - d.base)
    md, mr = boot(d, "diff"), boot(d, "r1")
    tg = d[d.base_reason == "target"]
    return {"n": len(d), "targets": len(tg), "base": float(d.base.mean()), "r1": float(d.r1.mean()),
            "diff": float(d["diff"].mean()), "p_diff": float((md <= 0).mean()),
            "r1_lower95": float(np.quantile(mr, 0.05)), "r1_win": float((d.r1 > 0).mean()),
            "on_targets": {"target_exit": float(tg.base.mean()) if len(tg) else math.nan,
                           "runner_all": float(tg.r_all.mean()) if len(tg) else math.nan,
                           "runner_reasons": tg.runner_why.value_counts().to_dict() if len(tg) else {}},
            "secondary": {"r_all": float(d.r_all.mean()), "r1_60": float(d.r1_60.mean()), "trail": float(d.trail.mean())}}


def main() -> int:
    bars = Bars()
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True)
    he = halt_events(bars)
    t = pd.DataFrame(trade_rows(fe, bars, False) + trade_rows(he, bars, True))
    t["split"] = np.where(t.session < D_STAR, "train", "holdout")
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "trades.csv", index=False)
    train = summary(t[t.split == "train"])
    powered = train["targets"] >= MIN_TARGETS
    passed = powered and train["diff"] > 0 and train["p_diff"] < ALPHA
    out = {"prereg": "docs/findings/prereg_long_runner_2026-11-11.md", "train": train, "powered": powered,
           "train_pass": passed, "holdout": None, "adopt": False, "profitable": False}
    if passed:
        h = summary(t[t.split == "holdout"])
        out["holdout"] = h
        out["adopt"] = h["diff"] > 0
        out["profitable"] = train["r1"] > 0 and train["r1_lower95"] > 0 and h["r1"] > 0
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="long-runner-2026-11-11", kind="long-runner",
                                  params={"prereg": out["prereg"]}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
