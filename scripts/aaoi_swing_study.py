"""AAOI daily "swing" capture (prereg_aaoi_swing_2026-11-12).

Three a-priori long-only candidates on daily RTH bars built from local 1-minute bars:
    C1  trend-day momentum continuation (cc >= +4% and close in top third) -> long next open, exit next close
    C2  oversold one-day bounce (cc <= -5%)                                 -> long next open, exit next close
    C3  20-session Donchian close breakout, non-overlapping 3-session hold  -> long next open, exit close+3

All decisions use only closed sessions <= T; entry is always the next session's open (no "buy the low"
hindsight). Training = entry_date <= 2024-12-31; held-out = 2025-01-01 .. latest, opened per candidate
only if that candidate passes on training. Primary cost 0.10% round trip, secondary 0.04%.
Day-clustered bootstrap (by entry date), 10,000 resamples, seed 20261112, Bonferroni alpha 0.05/3.

Usage (run ONCE, from the main checkout so data/alpaca + reports/ resolve):
    python scripts/aaoi_swing_study.py
    python scripts/aaoi_swing_study.py --data /abs/data/alpaca --out /abs/reports/aaoi_swing
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.intraday.store import ParquetBarStore

SYM = "AAOI"
OPEN_MIN, LAST_MIN = 570, 959                 # 09:30 and 15:59 ET bars
COST, COST_LOW = 0.0010, 0.0004               # round-trip fractions: primary 0.10%, secondary 0.04%
TRAIN_END = pd.Timestamp("2024-12-31")
HELD_START = pd.Timestamp("2025-01-01")
SEED = 20261112
N_BOOT = 10_000
K = 3                                         # candidates -> Bonferroni family
BONF_Q = 0.05 / K                             # per-candidate one-sided alpha
PLAIN_Q = 0.05
MIN_TRADES = 100
SPLIT_LO, SPLIT_HI = 0.4, 2.5                 # open/prev_close bounds (corporate-action guard)


def build_daily(f: pd.DataFrame) -> pd.DataFrame:
    """Daily RTH bar per session from 1-minute bars, plus the signal columns. No look-ahead:
    cc/pos/don20 are functions of closed sessions only."""
    f = f[(f.segment == "rth") & (f.minute_of_day >= OPEN_MIN) & (f.minute_of_day <= LAST_MIN)].copy()
    f["date"] = pd.to_datetime(f.session_date)
    g = f.groupby("date")
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                      "close": g.close.last(), "full": g.minute_of_day.max() == LAST_MIN}).sort_index()
    pc = d.close.shift(1)
    d["ratio"] = d.open / pc
    d["clean"] = d.ratio.between(SPLIT_LO, SPLIT_HI) | pc.isna()
    d["cc"] = d.close / pc - 1.0
    rng = d.high - d.low
    d["pos"] = np.where(rng > 0, (d.close - d.low) / rng, 0.5)
    d["don20"] = d.close.shift(1).rolling(20).max()
    return d.reset_index()


def _oneday(d: pd.DataFrame, mask: np.ndarray) -> pd.DataFrame:
    """Long open_{T+1} -> close_{T+1} for each signal row T (1-session hold)."""
    o, c = d.open.to_numpy(float), d.close.to_numpy(float)
    full, clean, dt = d.full.to_numpy(bool), d.clean.to_numpy(bool), d.date.to_numpy()
    rows = []
    for i in range(len(d) - 1):
        if not mask[i] or not (full[i] and clean[i]):
            continue
        j = i + 1
        if not (full[j] and clean[j]):
            continue
        rows.append({"entry_date": dt[j], "gross": c[j] / o[j] - 1.0})
    return pd.DataFrame(rows)


def c1_trades(d: pd.DataFrame) -> pd.DataFrame:
    mask = ((d.cc >= 0.04) & (d.pos >= 2.0 / 3.0)).to_numpy(bool)
    return _oneday(d, mask)


def c2_trades(d: pd.DataFrame) -> pd.DataFrame:
    mask = (d.cc <= -0.05).to_numpy(bool)
    return _oneday(d, mask)


def c3_trades(d: pd.DataFrame) -> pd.DataFrame:
    """20-session Donchian close breakout, non-overlapping 3-session hold: long open_{T+1} -> close_{T+3}."""
    o, c = d.open.to_numpy(float), d.close.to_numpy(float)
    close, don = d.close.to_numpy(float), d.don20.to_numpy(float)
    full, clean, dt = d.full.to_numpy(bool), d.clean.to_numpy(bool), d.date.to_numpy()
    n, next_allowed, rows = len(d), 0, []
    for i in range(n):
        if i < next_allowed or np.isnan(don[i]) or not (close[i] > don[i]):
            continue
        if not (full[i] and clean[i]):
            continue
        j, k = i + 1, i + 3
        if k >= n or not (full[j] and clean[j] and full[k] and clean[k]):
            continue
        rows.append({"entry_date": dt[j], "gross": c[k] / o[j] - 1.0})
        next_allowed = k + 1
    return pd.DataFrame(rows)


def hold_all(d: pd.DataFrame, h: int) -> pd.DataFrame:
    """Benchmark: long open_e -> close_{e+h-1} on EVERY eligible session (same horizon as the candidate)."""
    o, c = d.open.to_numpy(float), d.close.to_numpy(float)
    full, clean, dt = d.full.to_numpy(bool), d.clean.to_numpy(bool), d.date.to_numpy()
    rows = []
    for e in range(len(d) - (h - 1)):
        x = e + h - 1
        if not (full[e] and clean[e] and full[x] and clean[x]):
            continue
        rows.append({"entry_date": dt[e], "gross": c[x] / o[e] - 1.0})
    return pd.DataFrame(rows)


def boot_lb(gross: np.ndarray, dates: np.ndarray, cost: float, rng: np.random.Generator, q: float) -> float:
    """Day-clustered bootstrap lower bound of mean net (gross - cost), quantile q."""
    x = gross - cost
    if len(x) == 0:
        return float("nan")
    _, inv = np.unique(dates, return_inverse=True)
    s = np.bincount(inv, weights=x)
    nn = np.bincount(inv).astype(float)
    idx = rng.integers(0, len(nn), size=(N_BOOT, len(nn)))
    means = s[idx].sum(1) / nn[idx].sum(1)
    return float(np.quantile(means, q))


def _desc(t: pd.DataFrame, cost: float) -> dict:
    if len(t) == 0:
        return {"n": 0}
    net = t.gross.to_numpy(float) - cost
    return {"n": int(len(t)), "mean_net": float(net.mean()), "median_net": float(np.median(net)),
            "win": float((net > 0).mean()), "mean_gross": float(t.gross.mean())}


def evaluate(name: str, horizon: int, trades: pd.DataFrame, d: pd.DataFrame,
             rng: np.random.Generator) -> dict:
    tr = trades[trades.entry_date <= TRAIN_END]
    ho = trades[trades.entry_date >= HELD_START]
    hold = hold_all(d, horizon)
    hold_tr = hold[hold.entry_date <= TRAIN_END]
    hold_ho = hold[hold.entry_date >= HELD_START]
    res: dict = {"name": name, "horizon": horizon,
                 "train": {**_desc(tr, COST), "mean_net_0.04%": _desc(tr, COST_LOW).get("mean_net")},
                 "train_hold_bench": _desc(hold_tr, COST)}
    if len(tr):
        g, dts = tr.gross.to_numpy(float), tr.entry_date.to_numpy()
        res["train"]["bonf_lb"] = boot_lb(g, dts, COST, rng, BONF_Q)
        res["train"]["plain95_lb"] = boot_lb(g, dts, COST, rng, PLAIN_Q)
    gate_n = len(tr) >= MIN_TRADES
    gate_mean = bool(len(tr) and tr.gross.mean() - COST > 0)
    gate_lb = bool(len(tr) and res["train"].get("bonf_lb", float("-inf")) > 0)
    train_pass = gate_n and gate_mean and gate_lb
    res["train_gates"] = {"n>=100": gate_n, "mean_net>0": gate_mean, "bonf_lb>0": gate_lb,
                          "train_pass": train_pass}
    if train_pass:                                  # held-out opened only after a training pass
        res["held_out"] = {**_desc(ho, COST), "mean_net_0.04%": _desc(ho, COST_LOW).get("mean_net")}
        res["held_out_hold_bench"] = _desc(hold_ho, COST)
        res["verdict"] = "PASS" if (len(ho) and ho.gross.mean() - COST > 0) else "FAIL"
    else:
        res["held_out"] = "not opened (training did not pass)"
        res["verdict"] = "FAIL"
    return res


def _bh(d: pd.DataFrame, lo, hi) -> dict:
    x = d[(d.date >= lo) & (d.date <= hi) & d.full]
    if len(x) < 2:
        return {}
    return {"from": str(x.date.iloc[0].date()), "to": str(x.date.iloc[-1].date()),
            "buy_hold_total": float(x.close.iloc[-1] / x.open.iloc[0] - 1.0)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", default="data/alpaca")
    ap.add_argument("--out", default="reports/aaoi_swing")
    args = ap.parse_args()

    store = ParquetBarStore(args.data)
    f = store.read_frame(SYM, date(2016, 1, 1), date(2026, 12, 31))
    if f.empty:
        print(f"{SYM}: no local bars under {args.data}")
        return 1
    d = build_daily(f)
    last = d.date.max()
    if last < HELD_START:
        print(f"{SYM}: local data ends {last.date()} before held-out start; aborting (no improvised split)")
        return 1

    rng = np.random.default_rng(SEED)
    candidates = [("C1_momentum", 1, c1_trades(d)), ("C2_bounce", 1, c2_trades(d)),
                  ("C3_donchian", 3, c3_trades(d))]
    results = {"symbol": SYM, "last_session": str(last.date()), "cost_primary": COST, "cost_secondary": COST_LOW,
               "buy_hold_train": _bh(d, d.date.min(), TRAIN_END), "buy_hold_held_out": _bh(d, HELD_START, last),
               "candidates": {}}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, h, t in candidates:
        r = evaluate(name, h, t, d, rng)
        results["candidates"][name] = r
        if len(t):
            t.assign(net_primary=t.gross - COST).to_csv(out / f"trades_{name}.csv", index=False)
        tr, ho = r["train"], r.get("held_out")
        print(f"\n{name} (h={h}): train n={tr.get('n',0)} mean net {tr.get('mean_net',float('nan')):+.3%} "
              f"(gross {tr.get('mean_gross',float('nan')):+.3%}, @0.04% {tr.get('mean_net_0.04%',float('nan')):+.3%}) "
              f"bonf_lb {tr.get('bonf_lb',float('nan')):+.3%} plain95 {tr.get('plain95_lb',float('nan')):+.3%} "
              f"win {tr.get('win',float('nan')):.0%}")
        print(f"   train hold-bench net {r['train_hold_bench'].get('mean_net',float('nan')):+.3%} "
              f"(n {r['train_hold_bench'].get('n',0)}) | gates {r['train_gates']}")
        if isinstance(ho, dict):
            print(f"   held-out n={ho.get('n',0)} mean net {ho.get('mean_net',float('nan')):+.3%} | "
                  f"held-out hold-bench {r['held_out_hold_bench'].get('mean_net',float('nan')):+.3%}")
        print(f"   VERDICT {r['verdict']}")
    print(f"\nbuy&hold train {results['buy_hold_train']} held-out {results['buy_hold_held_out']}")
    (out / "result.json").write_text(json.dumps(results, indent=2, default=str))
    print(f"\nwrote {out / 'result.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
