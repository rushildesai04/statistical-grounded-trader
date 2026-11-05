"""Fidelity check (adversarial review 2026-11-05, F-1/F-2): do the trader's EMA crosses predict the next move?

trader_rules used EMA 9/24 while the trader said "9/21", and its 3-bar-low stop ended most trades within ~2 minutes,
so the entry signal was never measured on its own. This measures the forward move after each signal, with no stop
and no exit policy: enter at the next bar's open, mark at the close h minutes later (RTH only). Gross and net of the
measured gapper round-trip cost. Training period only (same universe and dates as trader_rules_study).

    python scripts/ema_cross_check.py --before 2026-04-01
"""
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
from sgt.decision.trader_rules import TraderRules, entries, indicators
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("ema_cross_check")
H = (5, 15, 30, 60)
SEED, N_BOOT = 20261105, 2_000
OUT = Path("reports/decision/ema_cross_check.json")


def _families(day: pd.DataFrame, ind: pd.DataFrame, rth: np.ndarray) -> dict[str, tuple[np.ndarray, int]]:
    """Signal bar indices per family and the trade side (+1 long, -1 short)."""
    c = day["close"]
    e9, e21 = ind["ema9"], c.ewm(span=21, adjust=False).mean()
    up21 = ((e9 > e21) & (e9.shift(1) <= e21.shift(1))).to_numpy() & rth
    dn21 = ((e9 < e21) & (e9.shift(1) >= e21.shift(1))).to_numpy() & rth
    up24 = ((e9 > ind["ema24"]) & (e9.shift(1) <= ind["ema24"].shift(1))).to_numpy() & rth
    ind21 = ind.assign(ema24=e21, gap_pct=(e9 - e21) / c)            # the trader's full entry, on 9/21
    full = TraderRules(window=(570, 959))
    out = {"cross_up_9_21": (np.flatnonzero(up21), 1), "cross_up_9_24": (np.flatnonzero(up24), 1),
           "cross_down_9_21_short": (np.flatnonzero(dn21), -1),
           "trader_full_9_21_all_day": (entries(day, ind21, full), 1),
           "trader_full_9_21_power_hour": (entries(day, ind21, TraderRules()), 1)}
    vol_ok = (day["volume"] / ind["vol_base"]).to_numpy() >= 3
    out["cross_up_9_21_vol3x"] = (np.flatnonzero(up21 & vol_ok), 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gappers", type=Path, default=Path("data/universe/gappers_2y.csv"))
    ap.add_argument("--before", type=date.fromisoformat, required=True)
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    days: dict[str, list[date]] = defaultdict(list)
    with a.gappers.open() as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["session"])
            if float(r["prev_volume"]) >= a.min_prev_volume and d < a.before:
                days[r["symbol"].upper()].append(d)
    store = ParquetBarStore("data/alpaca")
    rows = []
    for k, (sym, ds) in enumerate(sorted(days.items()), 1):
        for d in ds:
            f = store.read_frame(sym, d, d)
            if len(f) < 60:
                continue
            f = f.sort_values("ts").reset_index(drop=True)
            rth = (f["segment"].astype(str).str.lower().str.endswith("rth")).to_numpy()
            if not rth.any():
                continue
            ind = indicators(f)
            ts = pd.to_datetime(f.ts, utc=True)
            o, c = f.open.to_numpy(float), f.close.to_numpy(float)
            last = int(np.flatnonzero(rth)[-1])
            for fam, (idx, side) in _families(f, ind, rth).items():
                for i in idx:
                    i = int(i)
                    if i + 1 > last or o[i + 1] <= 0:
                        continue
                    entry = o[i + 1]
                    rt = cost.round_trip(sym, ts[i + 1].to_pydatetime(), entry)[0]
                    row = {"family": fam, "symbol": sym, "session": d.isoformat(), "cost": rt}
                    for h in H:
                        j = min(i + h, last)                   # mark at the close h bars after the signal bar
                        row[f"g{h}"] = side * (c[j] / entry - 1)
                    rows.append(row)
        if k % 200 == 0:
            log.info("%d/%d symbols, %d signals", k, len(days), len(rows))
    t = pd.DataFrame(rows)
    rng = np.random.default_rng(SEED)
    res = {}
    for fam, g in t.groupby("family"):
        sess = g.session.to_numpy()
        u, inv = np.unique(sess, return_inverse=True)
        fam_res = {"n": len(g), "sessions": len(u), "cost_mean": float(g.cost.mean())}
        for h in H:
            x = g[f"g{h}"].to_numpy(float)
            sums, cnt = np.bincount(inv, x, len(u)), np.bincount(inv, None, len(u))
            b = rng.integers(0, len(u), size=(N_BOOT, len(u)))
            means = sums[b].sum(1) / cnt[b].sum(1)
            fam_res[f"h{h}"] = {"gross": float(x.mean()), "net": float((x - g.cost).mean()),
                                "lo95": float(np.quantile(means, 0.025)), "hi95": float(np.quantile(means, 0.975)),
                                "win_net": float(((x - g.cost) > 0).mean())}
        res[fam] = fam_res
        print(f"{fam:30s} n {len(g):6d} cost {g.cost.mean():.2%} | " + " | ".join(
            f"{h}m gross {fam_res[f'h{h}']['gross']:+.2%} [{fam_res[f'h{h}']['lo95']:+.2%},"
            f"{fam_res[f'h{h}']['hi95']:+.2%}] net {fam_res[f'h{h}']['net']:+.2%}" for h in H))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"before": str(a.before), "families": res}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
