"""The 9/21 cross bought the instant volume is already there, on official prints.
(docs/findings/prereg_ema_instant_volume_2026-11-18.md)

    .venv/bin/python scripts/ema_instant_volume_study.py --fetch     # prints of the event minutes (cached, throttled)
    .venv/bin/python scripts/ema_instant_volume_study.py             # training, run ONCE

Report only: nothing here changes a book. The print API is external: rows are cleaned by ``clean_trades`` and bounded."""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ema_exact_cross_study import ALPHA, MARKS, WINDOWS, cell
from sgt.intraday.store import ParquetBarStore

EVENTS = Path("reports/ema_exact_cross/train_events.csv")
OUT = Path("reports/ema_instant_volume")
TICKS = Path("data/ticks")
SPIKE_X = 3.0
LATENCY_NS, FILL_WITHIN_NS = 1_000_000_000, 60 * 1_000_000_000
THROTTLE_S = 0.45                              # on top of the cache's own pause: about one request a second, so the
                                               # runner (same data account, 200 a minute) is never starved
EXITS = ("HOLD", "CUT")
PRICE_BUCKETS = (("below $1", 0, 1), ("$1-2", 1, 2), ("$2-5", 2, 5), ("$5-10", 5, 10), ("above $10", 10, np.inf))


def spike_events(path: Path = EVENTS) -> pd.DataFrame:
    """The SPIKE cross minutes of the exact-cross training run (the only minutes a trigger can fall in)."""
    t = pd.read_csv(path, converters={"symbol": str})      # 2026-11-18: the ticker NA is a ticker, not a missing value
    t = t[t.SPIKE.astype(bool)].copy()
    t["ts"] = pd.to_datetime(t.ts, utc=True)
    t["session"] = t.session.astype(str)
    return t.sort_values(["symbol", "session", "ts"], kind="stable").reset_index(drop=True)


def bases(store: ParquetBarStore, sym: str, session: str) -> dict[pd.Timestamp, float]:
    """Per bar of one session: the mean volume of the 10 minutes before it (as the exact-cross study)."""
    d = date.fromisoformat(session)
    f = store.read_frame(sym, d, d)
    if not len(f):
        return {}
    f = f.sort_values("ts").reset_index(drop=True)
    base = f.volume.astype(float).shift(1).rolling(10, min_periods=5).mean()
    return dict(zip(pd.to_datetime(f.ts, utc=True), base.to_numpy(float)))


def trigger(tr: np.ndarray, pstar: float, need_volume: float) -> int | None:
    """Row of the first print above the cross price once the minute has already traded ``need_volume``."""
    if not len(tr) or not need_volume > 0:
        return None
    hit = np.flatnonzero((tr[:, 1] > pstar) & (np.cumsum(tr[:, 2]) >= need_volume))
    return int(hit[0]) if hit.size else None


def fast_fill(tr: np.ndarray, t_trigger: float) -> float | None:
    """Price of the first print at least one second after the trigger, if one comes within a minute."""
    if not len(tr):
        return None
    i = int(np.searchsorted(tr[:, 0], t_trigger + LATENCY_NS, side="left"))
    if i >= len(tr) or tr[i, 0] > t_trigger + FILL_WITHIN_NS or not tr[i, 1] > 0:
        return None
    return float(tr[i, 1])


def score(e, base: float, this_minute: np.ndarray, next_minute) -> dict | None:
    """One event row (``e``: a row of the exact-cross event file). ``next_minute``: callable, fetched only if needed."""
    k = trigger(this_minute, float(e.pstar), SPIKE_X * base)
    if k is None:
        return None
    t0 = float(this_minute[k, 0])
    px = fast_fill(this_minute, t0)
    if px is None:
        nxt = next_minute()
        px = fast_fill(nxt, t0) if nxt is not None and len(nxt) else None
    row = {"symbol": e.symbol, "session": e.session, "ts": e.ts, "minute": int(e.minute), "fake": bool(e.fake),
           "pstar": float(e.pstar), "trig_s": (t0 - e.ts.value) / 1e9, "p_trig": float(this_minute[k, 1]),
           "p_FAST": np.nan if px is None else px, "p_NEXT": float(e.p_NEXT), "p_PLUS1": float(e.p_PLUS1), "cost": float(e.cost_EXACT)}
    for m in MARKS:
        ok = bool(getattr(e, f"ok_{m}")) and px is not None
        mark = float(getattr(e, f"m_{m}"))
        row[f"g_NEXT_{m}"] = mark / row["p_NEXT"] - 1 if ok else np.nan
        row[f"g_PLUS1_{m}"] = mark / row["p_PLUS1"] - 1 if ok else np.nan
        row[f"g_HOLD_{m}"] = mark / px - 1 if ok else np.nan
        row[f"g_CUT_{m}"] = ((row["p_NEXT"] if row["fake"] else mark) / px - 1) if ok else np.nan   # a failed cross is sold
    return row


def evaluate(t: pd.DataFrame, spikes: int) -> dict:
    gate_alpha = ALPHA / (len(EXITS) * len(MARKS))
    res: dict = {"spike_events": int(spikes), "triggered": int(len(t)), "traded": int(t.p_FAST.notna().sum()),
                 "fake_share": float(t.fake.mean()) if len(t) else None,
                 "trigger_second": {q: float(t.trig_s.quantile(v)) for q, v in (("p25", .25), ("median", .5), ("p75", .75))} if len(t) else None,
                 "cells": {}, "delay": {}, "held_vs_failed": {}, "by_price": {}, "by_window": {}, "by_cost_quartile": {}}
    for name in ("HOLD", "CUT", "NEXT", "PLUS1"):
        res["cells"][name] = {str(m): cell(t, f"g_{name}_{m}", "cost", gate_alpha if name in EXITS else None) for m in MARKS}
    traded = t[t.p_FAST.notna()]
    for name in ("NEXT", "PLUS1"):
        res["delay"][name] = cell(traded.assign(d=traded[f"p_{name}"] / traded.p_FAST - 1), "d", None) if len(traded) else None
    for label, part in (("failed", t[t.fake]), ("held", t[~t.fake])):
        res["held_vs_failed"][label] = {name: {str(m): cell(part, f"g_{name}_{m}", "cost") for m in MARKS} for name in ("HOLD", "CUT", "NEXT")}
    for label, lo, hi in PRICE_BUCKETS:
        w = t[(t.p_FAST >= lo) & (t.p_FAST < hi)]
        res["by_price"][label] = {name: {str(m): cell(w, f"g_{name}_{m}", "cost") for m in MARKS} for name in EXITS}
    for label, lo, hi in WINDOWS:
        w = t[(t.minute >= lo) & (t.minute <= hi)]
        res["by_window"][label] = {name: {str(m): cell(w, f"g_{name}_{m}", "cost") for m in MARKS} for name in EXITS}
    if len(traded) >= 8:
        q = pd.qcut(traded.cost, 4, labels=False, duplicates="drop")
        for k in sorted(q.dropna().unique()):
            w = traded[q == k]
            res["by_cost_quartile"][f"q{int(k) + 1}"] = {"cost_mean": float(w.cost.mean()), **{name: {str(m): cell(w, f"g_{name}_{m}", "cost") for m in MARKS} for name in EXITS}}
    bounds = {f"{name}_{m}": (res["cells"][name][str(m)] or {}).get("lower_one_sided") for name in EXITS for m in MARKS}
    res["gate"] = {"alpha_each": gate_alpha, "lower_bounds_net": bounds,
                   "pass": bool(any(b is not None and b > 0 for b in bounds.values()))}          # no bound = fail
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="only fetch and cache the prints (no number is computed)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    res_path = a.out / "result_train.json"
    if not a.fetch and res_path.is_file():
        print(f"{res_path} exists: the training run was made once and is not repeated")
        return 2
    import httpx

    from intrabar_study import TickCache

    ev = spike_events()
    with httpx.Client(timeout=30) as client:
        cache = TickCache(TICKS, client)
        if a.fetch:
            n = 0
            for r in ev.itertuples(index=False):
                if not (TICKS / r.symbol / f"{r.ts:%Y%m%d%H%M}.npy").exists():
                    cache.minute(r.symbol, r.ts.to_pydatetime())
                    n += 1
                    time.sleep(THROTTLE_S)
                    if n % 500 == 0:
                        print(f"fetched {n} minutes", flush=True)
            print(f"done: {n} minutes fetched, {len(ev)} events", flush=True)
            return 0
        store, rows = ParquetBarStore("data/alpaca"), []
        for (sym, session), g in ev.groupby(["symbol", "session"], sort=False):
            base = bases(store, sym, session)
            for e in g.itertuples(index=False):
                b = base.get(e.ts, np.nan)
                if not b > 0:
                    continue
                row = score(e, b, cache.minute(sym, e.ts.to_pydatetime()),
                            lambda e=e, sym=sym: cache.minute(sym, (e.ts + timedelta(minutes=1)).to_pydatetime()))
                if row is not None:
                    rows.append(row)
    t = pd.DataFrame(rows)
    if t.empty:
        raise SystemExit("no triggered event: nothing reported")
    res = {"prereg": "docs/findings/prereg_ema_instant_volume_2026-11-18.md", **evaluate(t, len(ev))}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    t.to_csv(a.out / "train_events.csv", index=False)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
