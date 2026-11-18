"""Is the best long entry EXACTLY when EMA 9 crosses 21, and what does a minute of delay cost?
(docs/findings/prereg_ema_exact_cross_2026-11-18.md)

    .venv/bin/python scripts/ema_exact_cross_study.py --before 2026-04-01      # training, run ONCE

Report only: nothing here changes a book. Bars only: the price at which the live EMA 9 passes the live EMA 21 inside a
minute follows from the two EMAs after the minute before it (``cross_price``)."""
from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.decision.features import CostModelAdapter
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("ema_exact_cross")
OUT = Path("reports/ema_exact_cross")
A9, A21 = 2 / 10, 2 / 22
MARKS = (5, 15, 30)
ENTRIES = {"EXACT": 0, "NEXT": 1, "PLUS1": 2, "PLUS2": 3, "PLUS5": 6}       # minutes after the cross minute
NEED_MIN = max(ENTRIES.values())
RULES = ("ALL", "RISING", "SPIKE")
RISING_X, SPIKE_X = 2.0, 3.0
WINDOWS = (("09:30-10:29", 570, 629), ("10:30-14:59", 630, 899), ("15:00-15:54", 900, 954))
ALPHA, SEED, N_BOOT = 0.05, 20261118, 10_000
ET = "America/New_York"
MIN_NS = 60 * 1_000_000_000


def cross_price(e9, e21):
    """The last price at which the live EMA 9 equals the live EMA 21, given both after the minute before."""
    return ((1 - A21) * e21 - (1 - A9) * e9) / (A9 - A21)


def scored(entry: str, mark: int) -> bool:
    """PLUS5 enters after the 5-minute mark, so it is not scored there (amendment 1)."""
    return ENTRIES[entry] <= mark


def day_events(f: pd.DataFrame) -> pd.DataFrame:
    """Every exact cross-up of one session (bars of all segments, time-ordered), with its entries and marks."""
    c, o, h, v = (f[k].to_numpy(float) for k in ("close", "open", "high", "volume"))
    ts = pd.to_datetime(f.ts, utc=True)
    ns = ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]").astype("int64")
    rth = f["segment"].astype(str).str.lower().str.endswith("rth").to_numpy()
    if not rth.any():
        return pd.DataFrame()
    close = pd.Series(c)
    e9, e21 = close.ewm(span=9, adjust=False).mean().to_numpy(), close.ewm(span=21, adjust=False).mean().to_numpy()
    vol = pd.Series(v)
    base = vol.shift(1).rolling(10, min_periods=5).mean().to_numpy()         # the 10 minutes before each minute
    ri = np.flatnonzero(rth)                                                 # regular-hours bars only, for fills
    rns, last_ns = ns[ri], ns[ri[-1]]
    et = ts.dt.tz_convert(ET)
    minute = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    rows = []
    for i in ri:
        if i == 0 or not e9[i - 1] <= e21[i - 1]:
            continue
        p = cross_price(e9[i - 1], e21[i - 1])
        if not (p > 0 and h[i] >= p) or ns[i] + NEED_MIN * MIN_NS > last_ns:
            continue
        row = {"ts": ts.iloc[i], "minute": int(minute[i]), "pstar": float(p), "fake": bool(not e9[i] > e21[i]),
               "RISING": bool(i >= 2 and base[i - 1] > 0 and v[i - 1] >= RISING_X * base[i - 1]),
               "SPIKE": bool(base[i] > 0 and v[i] >= SPIKE_X * base[i]), "p_EXACT": float(max(p, o[i])), "b_EXACT": int(ns[i])}
        for name, k in ENTRIES.items():
            if k:
                j = ri[int(np.searchsorted(rns, ns[i] + k * MIN_NS, side="left"))]   # the first bar at or after it
                row[f"p_{name}"], row[f"b_{name}"] = float(o[j]), int(ns[j])
        for m in MARKS:
            j = ri[int(np.searchsorted(rns, ns[i] + m * MIN_NS, side="right")) - 1]  # the last bar at or before it
            row[f"m_{m}"], row[f"mb_{m}"] = float(c[j]), int(ns[j])
        rows.append(row)
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    for m in MARKS:                                                          # the same events for every entry scored
        ok = np.ones(len(t), bool)
        for name in ENTRIES:
            if scored(name, m):
                ok &= (t[f"b_{name}"] <= t[f"mb_{m}"]).to_numpy() & (t[f"p_{name}"] > 0).to_numpy()
        t[f"ok_{m}"] = ok
        for name in ENTRIES:
            if scored(name, m):
                t[f"g_{name}_{m}"] = np.where(ok, t[f"m_{m}"] / t[f"p_{name}"] - 1, np.nan)
    return t


def boot_means(x: np.ndarray, inv: np.ndarray, n_sessions: int, seed: int = SEED) -> np.ndarray | None:
    """Session-bootstrap distribution of the mean of ``x`` (a resample draws whole sessions)."""
    if not len(x) or n_sessions < 2:
        return None
    sums, cnt = np.bincount(inv, x, n_sessions), np.bincount(inv, None, n_sessions)
    b = np.random.default_rng(seed).integers(0, n_sessions, size=(N_BOOT, n_sessions))
    den = cnt[b].sum(1)
    return sums[b].sum(1)[den > 0] / den[den > 0]


def cell(g: pd.DataFrame, col: str, cost: str | None, alpha: float | None = None) -> dict | None:
    """Mean of one column over its events; with ``cost`` also net and win rate; ``alpha`` adds a one-sided bound."""
    x = g[col].to_numpy(float)
    keep = np.isfinite(x)
    if not keep.any():
        return None
    x, sess = x[keep], g.session.to_numpy()[keep]
    u, inv = np.unique(sess, return_inverse=True)
    out = {"n": int(len(x)), "sessions": int(len(u)), "mean": float(x.mean()), "median": float(np.median(x)),
           "share_pos": float((x > 0).mean())}
    target = x
    if cost is not None:
        net = x - g[cost].to_numpy(float)[keep]
        out.update(net=float(net.mean()), win_net=float((net > 0).mean()))
        target = net
    means = boot_means(target, inv, len(u))
    if means is not None and len(means):
        out["lo95"], out["hi95"] = float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))
        if alpha is not None:
            out["lower_one_sided"] = float(np.quantile(means, alpha))
    return out


def subset(t: pd.DataFrame, rule: str) -> pd.DataFrame:
    return t if rule == "ALL" else t[t[rule]]


def evaluate(t: pd.DataFrame) -> dict:
    res: dict = {"events": int(len(t)), "sessions": int(t.session.nunique()), "symbols": int(t.symbol.nunique()), "rules": {}}
    gate_alpha = ALPHA / len(MARKS)
    for rule in RULES:
        g = subset(t, rule)
        r: dict = {"events": int(len(g)), "fake_share": float(g.fake.mean()) if len(g) else None, "entries": {}, "delay": {},
                   "fake_vs_confirmed": {}, "by_window": {}}
        for name in ENTRIES:
            r["entries"][name] = {str(m): cell(g, f"g_{name}_{m}", f"cost_{name}", gate_alpha if name == "EXACT" else None)
                                  for m in MARKS if scored(name, m)}
            if name != "EXACT":
                d = g.assign(d=g[f"p_{name}"] / g.p_EXACT - 1)
                r["delay"][name] = cell(d, "d", None)
        for label, part in (("fake", g[g.fake]), ("confirmed", g[~g.fake])):
            r["fake_vs_confirmed"][label] = {name: {str(m): cell(part, f"g_{name}_{m}", f"cost_{name}") for m in MARKS}
                                             for name in ("EXACT", "NEXT")}
        for label, lo, hi in WINDOWS:
            w = g[(g.minute >= lo) & (g.minute <= hi)]
            r["by_window"][label] = {"events": int(len(w)),
                                     "EXACT": {str(m): cell(w, f"g_EXACT_{m}", "cost_EXACT") for m in MARKS},
                                     "delay_NEXT": cell(w.assign(d=w.p_NEXT / w.p_EXACT - 1), "d", None) if len(w) else None}
        res["rules"][rule] = r
    bounds = {str(m): (res["rules"]["RISING"]["entries"]["EXACT"][str(m)] or {}).get("lower_one_sided") for m in MARKS}
    res["claim_a"] = {"rule": "RISING", "entry": "EXACT", "alpha_each": gate_alpha, "lower_bounds_net": bounds,
                      "pass": bool(any(b is not None and b > 0 for b in bounds.values()))}   # no bound = fail
    return res


def collect(days: dict[str, list[date]], store: ParquetBarStore, cost) -> pd.DataFrame:
    parts = []
    for k, (sym, ds) in enumerate(sorted(days.items()), 1):
        for d in ds:
            f = store.read_frame(sym, d, d)
            if len(f) < 60:
                continue
            t = day_events(f.sort_values("ts").reset_index(drop=True))
            if t.empty:
                continue
            for name in ENTRIES:
                at = pd.to_datetime(t[f"b_{name}"], utc=True)
                t[f"cost_{name}"] = [cost.round_trip(sym, a.to_pydatetime(), p)[0] if p > 0 else np.nan
                                     for a, p in zip(at, t[f"p_{name}"])]
            parts.append(t.assign(symbol=sym, session=d.isoformat()))
        if k % 200 == 0:
            log.info("%d/%d symbols, %d events", k, len(days), sum(len(p) for p in parts))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gappers", type=Path, default=Path("data/universe/gappers_2y.csv"))
    ap.add_argument("--before", type=date.fromisoformat, required=True)
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    res_path = a.out / "result_train.json"
    if res_path.is_file():
        print(f"{res_path} exists: the training run was made once and is not repeated")
        return 2
    if a.before > date(2026, 4, 1):
        print("sessions from 2026-04-01 on stay closed (prereg): --before may not be later")
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT, rest_mult=GAPPER_REST_MULT)
    days: dict[str, list[date]] = defaultdict(list)
    with a.gappers.open() as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["session"])
            if float(r["prev_volume"]) >= a.min_prev_volume and d < a.before:
                days[r["symbol"].upper()].append(d)
    t = collect(days, ParquetBarStore("data/alpaca"), cost)
    if t.empty:
        raise SystemExit("no events: nothing reported")
    res = {"prereg": "docs/findings/prereg_ema_exact_cross_2026-11-18.md", "before": str(a.before), **evaluate(t)}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    t.drop(columns=[c for c in t.columns if c.startswith(("b_", "mb_"))]).to_csv(a.out / "train_events.csv", index=False)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
