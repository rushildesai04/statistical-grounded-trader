"""When to leave a short, when to enter a long: both mapped on every tested alert
(docs/findings/prereg_timing_maps_2026-11-17.md).

    .venv/bin/python scripts/fade_timing_map.py            # training map, run ONCE; held-out only for a finding

Report only: nothing here changes a book."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from fade_challengers import _rth, events
from sgt.fast.exits import CHAMPION, indicators, simulate
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/timing_maps")
STALE = (5, 10, 15, 20)
PROFIT = ((0.0, 0.05, "0 to 5%"), (0.05, 0.10, "5 to 10%"), (0.10, 9.0, "10% or more"))
ENTRY_K = (0, 5, 10, 15, 20, 30, 45, 60, 90)
REL = ((-9.0, -0.10, "down 10% or more"), (-0.10, 0.0, "down less than 10%"), (0.0, 9.0, "above the alert price"))
HOLD_MIN, ENTRY_TOL, LAST_ENTRY_MIN = 30, 5, 15 * 60 + 30
MIN_A, MIN_B, ALPHA, SEED, N_BOOT = 150, 300, 0.05, 20261117, 10_000
ET = "America/New_York"


def stall_moments(bars: pd.DataFrame, entry_ts: pd.Timestamp, entry: float) -> list[dict]:
    """Map A for one event: for each N, the first closed bar (while the champion still holds) whose lowest low since
    entry is at least N minutes old and whose close is below the entry. Returns rows with the profit there, the
    result if covered at the next bar's open, and the champion's result on the trade."""
    ind = indicators(bars)
    ch = simulate(ind, entry_ts, entry, CHAMPION)
    if ch is None:
        return []
    i0 = int(np.flatnonzero((ind.ts >= entry_ts).to_numpy())[0])
    end = i0 + ch["minutes"] - 1                     # the champion's exit bar: nothing is decided on or after it
    o, lo, c = (ind[k].to_numpy(float) for k in ("open", "low", "close"))
    ts = ind.ts.reset_index(drop=True)
    out, done, low, low_i = [], set(), np.inf, i0
    for i in range(i0, end):                         # bars strictly before the exit bar; i + 1 exists
        if lo[i] < low:
            low, low_i = lo[i], i
        stale = (ts[i] - ts[low_i]).total_seconds() / 60
        if c[i] >= entry:
            continue
        for n in STALE:
            if n not in done and stale >= n:
                done.add(n)
                cover = float(o[i + 1])
                out.append({"n": n, "profit": (entry - c[i]) / entry, "cover": (entry - cover) / entry,
                            "hold": ch["gross"], "how": ch["how"], "minute": i - i0 + 1})
    return out


def long_entries(bars: pd.DataFrame, entry_ts: pd.Timestamp, entry: float) -> list[dict]:
    """Map B for one event: for each k, a long bought at the open of the first bar at or after entry + k minutes
    (within ENTRY_TOL, not after 15:30 ET) and sold at the last close within HOLD_MIN minutes."""
    b = bars.reset_index(drop=True)
    out = []
    for k in ENTRY_K:
        t = entry_ts + pd.Timedelta(minutes=k)
        later = b[b.ts >= t]
        if later.empty:
            continue
        first = later.iloc[0]
        et = first.ts.tz_convert(ET)
        if first.ts - t > pd.Timedelta(minutes=ENTRY_TOL) or et.hour * 60 + et.minute > LAST_ENTRY_MIN:
            continue
        path = b[(b.ts >= first.ts) & (b.ts < first.ts + pd.Timedelta(minutes=HOLD_MIN))]
        px = float(first.open)
        if px <= 0 or path.empty:
            continue
        out.append({"k": k, "rel": px / entry - 1, "gross": float(path.close.iloc[-1]) / px - 1})
    return out


def collect(ev: pd.DataFrame, store: ParquetBarStore) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    a, b, base = [], [], []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, str(r.session))
        if bars.empty:
            continue
        ts, entry = pd.Timestamp(r.entry_ts), float(r.entry)
        key = {"symbol": r.symbol, "session": str(r.session), "cost": float(r.cost_rt)}
        ch = simulate(indicators(bars), ts, entry, CHAMPION)
        if ch is not None:
            base.append({**key, "hold": ch["gross"]})
        a += [{**key, **x} for x in stall_moments(bars, ts, entry)]
        b += [{**key, **x} for x in long_entries(bars, ts, entry)]
    return pd.DataFrame(a), pd.DataFrame(b), pd.DataFrame(base)


def bucket(x: pd.Series, edges) -> pd.Series:
    out = pd.Series(index=x.index, dtype=object)
    for lo, hi, name in edges:
        out[(x >= lo) & (x < hi)] = name
    return out


def bound(v: pd.Series, session: pd.Series, q: float) -> float:
    """Session-bootstrap quantile ``q`` of the mean of ``v`` (sessions resampled whole)."""
    g = pd.DataFrame({"v": v.to_numpy(float), "s": session.to_numpy()}).groupby("s").v.agg(["sum", "count"])
    s, n = g["sum"].to_numpy(), g["count"].to_numpy()
    idx = np.random.default_rng(SEED).integers(0, len(g), size=(N_BOOT, len(g)))
    return float(np.quantile(s[idx].sum(1) / n[idx].sum(1), q))


def map_a(a: pd.DataFrame) -> list[dict]:
    """Cells of map A. ``cont`` = hold - cover; a finding needs MIN_A events, mean < 0 and upper bound < 0."""
    if a.empty:
        return []
    a = a.assign(cell=bucket(a.profit, PROFIT), cont=a.hold - a.cover)
    alpha = ALPHA / (len(STALE) * len(PROFIT))
    rows = []
    for (n, cell), g in a.groupby(["n", "cell"], sort=True):
        up = bound(g.cont, g.session, 1 - alpha) if len(g) >= 2 else float("nan")
        rows.append({"stale_min": int(n), "profit_then": cell, "events": int(len(g)), "cover": float(g.cover.mean()),
                     "hold": float(g.hold.mean()), "continuation": float(g.cont.mean()), "upper": up,
                     "held_then_stopped": float((g.how == "stop").mean()), "median_minute": float(g.minute.median()),
                     "finding": bool(len(g) >= MIN_A and g.cont.mean() < 0 and up < 0)})
    return rows


def rules_a(a: pd.DataFrame, base: pd.DataFrame) -> list[dict]:
    """STALL-N as a whole rule on all events: covered at its moment when there is one, else as the champion."""
    out = []
    for n in STALE:
        hit = a[a.n == n].set_index(["symbol", "session"]).cover if len(a) else pd.Series(dtype=float)
        b = base.set_index(["symbol", "session"])
        net = (hit.reindex(b.index).fillna(b.hold) - b.cost - 0.005)
        out.append({"rule": f"STALL-{n}", "mean": float(net.mean()), "champion": float((b.hold - b.cost - 0.005).mean()),
                    "ended_by_rule": float(hit.reindex(b.index).notna().mean()), "events": int(len(b))})
    return out


def map_b(b: pd.DataFrame) -> list[dict]:
    if b.empty:
        return []
    b = b.assign(cell=bucket(b.rel, REL), net=b.gross - b.cost)
    alpha = ALPHA / (len(ENTRY_K) * len(REL))
    rows = []
    for (k, cell), g in b.groupby(["k", "cell"], sort=True):
        lo = bound(g.net, g.session, alpha) if len(g) >= 2 else float("nan")
        rows.append({"minutes_after": int(k), "price_vs_alert": cell, "events": int(len(g)), "gross": float(g.gross.mean()),
                     "net": float(g.net.mean()), "win": float((g.net > 0).mean()), "lower": lo,
                     "finding": bool(len(g) >= MIN_B and g.net.mean() > 0 and lo > 0)})
    all_k = [{"minutes_after": int(k), "events": int(len(g)), "gross": float(g.gross.mean()), "net": float(g.net.mean()),
              "win": float((g.net > 0).mean())} for k, g in b.groupby("k", sort=True)]
    return rows + [{"all_prices": all_k}]


def main() -> int:
    res_path = OUT / "result_train.json"
    if res_path.is_file():
        print(f"{res_path} exists: the training map was made once and is not repeated")
        return 2
    store = ParquetBarStore("data/alpaca")
    a, b, base = collect(events("train"), store)
    res = {"prereg": "docs/findings/prereg_timing_maps_2026-11-17.md", "events": int(len(base)),
           "map_a": map_a(a), "rules_a": rules_a(a, base), "map_b": map_b(b)}
    found_a = [r for r in res["map_a"] if r.get("finding")]
    found_b = [r for r in res["map_b"] if r.get("finding")]
    res["findings"] = {"shorts": found_a, "longs": found_b}
    if found_a or found_b:                                  # the held-out period is read only for a finding
        ha, hb, hbase = collect(events("holdout"), store)
        keep_a = {(r["stale_min"], r["profit_then"]) for r in found_a}
        keep_b = {(r["minutes_after"], r["price_vs_alert"]) for r in found_b}
        res["holdout"] = {"map_a": [r for r in map_a(ha) if (r["stale_min"], r["profit_then"]) in keep_a],
                          "map_b": [r for r in map_b(hb) if (r.get("minutes_after"), r.get("price_vs_alert")) in keep_b]}
    OUT.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
