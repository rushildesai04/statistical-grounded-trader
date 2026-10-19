"""The trader's own rules (sgt.decision.trader_rules) by time window, training period only.

Usage:
    python scripts/trader_rules_study.py --before 2026-04-01
"""
from __future__ import annotations

import argparse
import csv
import logging
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.core.types import Segment
from sgt.decision import validation as val
from sgt.decision.features import CostModelAdapter
from sgt.decision.trader_rules import TraderRules, entries, indicators, trade
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("trader_rules_study")
WINDOWS = {"open_hour": (570, 629), "midday": (630, 899), "power_hour": (900, 950),
           "after_hours": (960, 1170)}
GRID = [TraderRules(), replace(TraderRules(), vol_spike=3.0), replace(TraderRules(), rsi_max=80.0),
        replace(TraderRules(), vol_spike=3.0, rsi_max=80.0)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gappers", type=Path, default=Path("data/universe/gappers_2y.csv"))
    ap.add_argument("--before", type=date.fromisoformat, required=True)
    ap.add_argument("--after", type=date.fromisoformat, default=date(1900, 1, 1))
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    ap.add_argument("--out", type=Path, default=Path("reports/decision/trader_rules"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    days: dict[str, list[date]] = defaultdict(list)
    with args.gappers.open() as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["session"])
            if float(r["prev_volume"]) >= args.min_prev_volume and args.after <= d < args.before:
                days[r["symbol"].upper()].append(d)
    store = ParquetBarStore("data/alpaca")
    rows = []
    for k, (sym, ds) in enumerate(sorted(days.items()), 1):
        for d in ds:
            bars = store.read(sym, d, d)
            if len(bars) < 60:
                continue
            day = pd.DataFrame({"open": [b.open for b in bars], "high": [b.high for b in bars],
                                "low": [b.low for b in bars], "close": [b.close for b in bars],
                                "volume": [b.volume for b in bars],
                                "minute_of_day": [b.minute_of_day for b in bars]})
            # explicit bool mask: np.array() of the Segment enum compares as strings
            rth_idx = np.flatnonzero(np.array([b.segment is Segment.RTH for b in bars]))
            if rth_idx.size == 0:
                continue
            ind = indicators(day)
            for wname, win in WINDOWS.items():
                last_i = int(rth_idx[-1]) if wname != "after_hours" else len(bars) - 1
                for g, rules in enumerate(GRID):
                    busy = -1
                    for t in entries(day, ind, replace(rules, window=win)):
                        if t <= busy:
                            continue
                        f = trade(day, ind, int(t), last_i)
                        if f is None:
                            continue
                        busy = f.exit_i
                        hin = 0.5 * cost.round_trip(sym, bars[f.entry_i].ts, f.entry)[0]
                        hout = 0.5 * cost.round_trip(sym, bars[f.exit_i].ts, f.exit)[0]
                        net = (f.exit * (1 - hout)) / (f.entry * (1 + hin)) - 1
                        rows.append({"symbol": sym, "session_date": d.isoformat(), "window": wname,
                                     "grid": g, "gross": f.exit / f.entry - 1, "net": net,
                                     "hold_min": f.exit_i - f.entry_i, **asdict(f)})
        if k % 300 == 0:
            log.info("%d/%d symbols, %d trades", k, len(days), len(rows))
    df = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out.with_suffix(".trades.csv"), index=False)
    lines = [f"# Trader's rules by window ({args.after}..{args.before}, gap days)", "",
             "| window | grid | trades | gross | net | hit (net) | avg win | avg loss | median hold | 90% CI net (day-clustered) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for (wname, g), x in df.groupby(["window", "grid"]):
        w, lo = x.net[x.net > 0], x.net[x.net <= 0]
        ci = val.cluster_bootstrap_ci(x.net.to_numpy(), x.session_date.to_numpy(), alpha=0.10)
        lines.append(f"| {wname} | {g} {GRID[g].vol_spike:g}x rsi<{GRID[g].rsi_max:g} | {len(x)} | "
                     f"{100 * x.gross.mean():+.3f}% | {100 * x.net.mean():+.3f}% | {(x.net > 0).mean():.0%} | "
                     f"{100 * w.mean():+.2f}% | {100 * lo.mean():+.2f}% | {x.hold_min.median():.0f}m | "
                     f"[{100 * ci[0]:+.3f}%, {100 * ci[1]:+.3f}%] |")
    report = "\n".join(lines) + "\n"
    args.out.with_suffix(".report.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
