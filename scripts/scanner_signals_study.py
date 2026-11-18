"""Do our own alert signals on the alphas carry an edge? Ten years of minute candles.
(docs/findings/prereg_scanner_signals_2026-11-18.md)

    .venv/bin/python scripts/scanner_signals_study.py              # training, run ONCE
    .venv/bin/python scripts/scanner_signals_study.py --holdout    # only what passed on training, once

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/scanner_signals")
GATE = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA", "TSLA", "AMD", "AVGO", "COST", "JPM", "LLY", "MA", "NFLX", "ORCL",
        "UNH", "V", "WMT", "XOM", "RDDT")
APART = ("AAOI",)
COST, COST_BY = 0.0004, {"RDDT": 0.0010, "AAOI": 0.0030}
SPLITS = {"train": ("2016-01-04", "2024-12-31"), "holdout": ("2025-01-02", "2026-10-30")}
KINDS = ("spike", "extreme", "burst", "cross")
EXITS = ("15", "60", "close")
FIRST_MIN, LAST_MIN = 9 * 60 + 45, 15 * 60 + 25          # a candle starting in this ET window can signal
SPIKE_V, SPIKE_S, EXT_V, EXT_BARS, BURST_S, BURST_N, CROSS_V, CROSS_BARS = 5.0, 2.0, 2.0, 30, 4.0, 5, 3.0, 45
MIN_DAY_BARS, MIN_CELL = 60, 300
ALPHA, SEED, N_BOOT = 0.05, 20261118, 10_000
WINDOWS = (("09:45-10:30", FIRST_MIN, 630), ("10:30-14:00", 630, 840), ("14:00-15:25", 840, LAST_MIN + 1))
ET = "America/New_York"


def cost(symbol: str) -> float:
    return COST_BY.get(symbol, COST)


def masks(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, v: np.ndarray) -> dict[tuple[str, int], np.ndarray]:
    """Per signal (kind, direction): the candles on which it is true, from closed candles only."""
    n = len(c)
    close = pd.Series(c)
    r1 = close.pct_change()
    s = r1.rolling(30, min_periods=15).std().shift(1)                 # the stock's own noise before this candle
    v20 = pd.Series(v).rolling(20, min_periods=10).mean().shift(1)
    i = np.arange(n)
    with np.errstate(invalid="ignore"):
        big = (v >= SPIKE_V * v20) & (r1.abs() >= SPIKE_S * s) & (s > 0)
        prior_hi, prior_lo = pd.Series(h).cummax().shift(1), pd.Series(lo).cummin().shift(1)
        ext = (i >= EXT_BARS) & (v >= EXT_V * v20)
        r5 = close / close.shift(BURST_N) - 1
        burst = (r5.abs() >= BURST_S * s * np.sqrt(BURST_N)) & (s > 0)
        e9, e21 = close.ewm(span=9, adjust=False).mean(), close.ewm(span=21, adjust=False).mean()
        above = e9 > e21
        crossed = (i >= CROSS_BARS) & (v >= CROSS_V * v20)
        out = {("spike", 1): big & (r1 > 0), ("spike", -1): big & (r1 < 0),
               ("extreme", 1): ext & (close > prior_hi), ("extreme", -1): ext & (close < prior_lo),
               ("burst", 1): burst & (r5 > 0), ("burst", -1): burst & (r5 < 0),
               ("cross", 1): crossed & above & ~above.shift(1, fill_value=False) & e21.shift(1).notna(),
               ("cross", -1): crossed & ~above & above.shift(1, fill_value=True) & e21.shift(1).notna()}
    return {k: np.asarray(m.fillna(False) if isinstance(m, pd.Series) else m, dtype=bool) for k, m in out.items()}


def day_signals(o, h, lo, c, v, minute) -> list[dict]:
    """The first signal of each kind and direction of one stock-day, with the trade that scores it (gross)."""
    n = len(c)
    if n < MIN_DAY_BARS:
        return []
    window = (minute >= FIRST_MIN) & (minute <= LAST_MIN)
    rows = []
    for (kind, d), m in masks(o, h, lo, c, v).items():
        hit = np.flatnonzero(m & window & (np.arange(n) + 1 < n))
        if not hit.size:
            continue
        i = int(hit[0])
        entry = o[i + 1]
        if not entry > 0:
            continue
        g = {"15": d * (c[min(i + 15, n - 1)] / entry - 1), "60": d * (c[min(i + 60, n - 1)] / entry - 1),
             "close": d * (c[n - 1] / entry - 1)}
        rows.append({"kind": kind, "dir": d, "minute": int(minute[i]), **{f"g_{k}": float(x) for k, x in g.items()}})
    return rows


def collect(symbols, store: ParquetBarStore, start: str, end: str) -> pd.DataFrame:
    rows = []
    y0, y1 = int(start[:4]), int(end[:4])
    for sym in symbols:
        for year in range(y0, y1 + 1):
            a, b = max(date(year, 1, 1), date.fromisoformat(start)), min(date(year, 12, 31), date.fromisoformat(end))
            f = store.read_frame(sym, a, b)
            if not len(f):
                continue
            f = f[f.segment == "rth"]
            ts = pd.to_datetime(f.ts, utc=True).dt.tz_convert(ET)
            f = f.assign(day=ts.dt.strftime("%Y-%m-%d").to_numpy(), minute=(ts.dt.hour * 60 + ts.dt.minute).to_numpy(),
                         _ts=ts.to_numpy()).sort_values("_ts")
            for day, g in f.groupby("day", sort=False):
                for r in day_signals(*(g[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume")),
                                     g.minute.to_numpy()):
                    rows.append({"symbol": sym, "day": day, "cost": cost(sym), **r})
    return pd.DataFrame(rows)


class Boot:
    """Bootstrap of a mean by session date, one draw of dates shared by every cell (same seed, same resamples)."""

    def __init__(self, days: np.ndarray) -> None:
        self.u = np.unique(days)
        self.idx = np.random.default_rng(SEED).integers(0, len(self.u), size=(N_BOOT, len(self.u)), dtype=np.int32)

    def means(self, days: np.ndarray, val: np.ndarray) -> np.ndarray | None:
        if len(val) < 2:
            return None
        pos = np.searchsorted(self.u, days)
        s, n = np.bincount(pos, val, len(self.u)), np.bincount(pos, minlength=len(self.u)).astype(float)
        den = n[self.idx].sum(1)
        ok = den > 0
        return s[self.idx].sum(1)[ok] / den[ok] if ok.any() else None


def cell(g: pd.DataFrame, exit_: str, boot: Boot, alpha: float, n_days: int) -> dict:
    x, c, days = g[f"g_{exit_}"].to_numpy(float), g.cost.to_numpy(float), g.day.to_numpy()
    out = {"signals": int(len(x)), "per_day": float(len(x) / n_days) if n_days else None,
           "gross": float(x.mean()) if len(x) else None, "win": float((x > 0).mean()) if len(x) else None,
           "follow_net": float((x - c).mean()) if len(x) else None, "fade_net": float((-x - c).mean()) if len(x) else None,
           "follow_net_2x": float((x - 2 * c).mean()) if len(x) else None, "fade_net_2x": float((-x - 2 * c).mean()) if len(x) else None,
           "follow_lower": None, "fade_lower": None, "follow_pass": False, "fade_pass": False}
    if len(x) >= MIN_CELL:
        fm, dm = boot.means(days, x - c), boot.means(days, -x - c)
        if fm is not None and dm is not None:
            out["follow_lower"], out["fade_lower"] = float(np.quantile(fm, alpha)), float(np.quantile(dm, alpha))
            out["follow_pass"], out["fade_pass"] = bool(out["follow_lower"] > 0), bool(out["fade_lower"] > 0)
    return out


def name(kind: str, d: int) -> str:
    return f"{kind} {'up' if d > 0 else 'down'}"


def evaluate(t: pd.DataFrame, n_days: int, alpha: float, only: list[str] | None = None) -> dict:
    """Every cell of the gate set (or only the named cells ``"<signal> | <exit> | follow|fade"``)."""
    boot = Boot(t.day.to_numpy())
    res: dict = {"signals": int(len(t)), "days": int(n_days), "alpha_each": alpha, "cells": {}, "passes": []}
    for (kind, d), g in t.groupby(["kind", "dir"], sort=True):
        for e in EXITS:
            key = f"{name(kind, d)} | {e}"
            if only is not None and not any(o.startswith(key + " |") for o in only):
                continue
            cl = cell(g, e, boot, alpha, n_days)
            cl["by_year"] = {y: {"n": int(len(p)), "gross": float(p[f"g_{e}"].mean())} for y, p in g.groupby(g.day.str[:4])}
            cl["by_stock"] = {s: {"n": int(len(p)), "gross": float(p[f"g_{e}"].mean())} for s, p in g.groupby("symbol")}
            cl["by_time"] = {w: {"n": int(len(p)), "gross": float(p[f"g_{e}"].mean()) if len(p) else None}
                             for w, a, b in WINDOWS for p in [g[(g.minute >= a) & (g.minute < b)]]}
            res["cells"][key] = cl
            for side in ("follow", "fade"):
                if cl[f"{side}_pass"] and (only is None or f"{key} | {side}" in only):
                    res["passes"].append(f"{key} | {side}")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--holdout", action="store_true", help="the held-out sessions, once, only for what passed on training")
    a = ap.parse_args(argv)
    split = "holdout" if a.holdout else "train"
    res_path = a.out / f"result_{split}.json"
    if res_path.is_file():
        print(f"{res_path} exists: the {split} run was made once and is not repeated")
        return 2
    only = None
    if a.holdout:
        train = a.out / "result_train.json"
        only = json.loads(train.read_text()).get("passes") if train.is_file() else None
        if not only:
            print("the held-out sessions are opened only for something that passed on training")
            return 2
    start, end = SPLITS[split]
    store = ParquetBarStore("data/alpaca")
    t = collect(GATE, store, start, end)
    if t.empty:
        raise SystemExit("no signals: nothing reported")
    n_days = int(t.day.nunique())
    alpha = ALPHA / (len(only) if only else 2 * len(KINDS) * 2 * len(EXITS))
    res = {"prereg": "docs/findings/prereg_scanner_signals_2026-11-18.md", "split": split, "span": [start, end],
           **evaluate(t, n_days, alpha, only)}
    apart = collect(APART, store, start, end)
    res["apart"] = {s: {f"{name(k, d)} | {e}": {"n": int(len(g)), "gross": float(g[f"g_{e}"].mean()),
                                                "follow_net": float((g[f"g_{e}"] - g.cost).mean())}
                        for (k, d), g in p.groupby(["kind", "dir"]) for e in EXITS} for s, p in apart.groupby("symbol")} if len(apart) else {}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    t.to_parquet(a.out / f"{split}_signals.parquet")
    print(json.dumps({k: v for k, v in res.items() if k != "cells"}, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
