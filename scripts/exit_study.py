"""Same entries, four exit rules, on in-play gap days (training period only).

Usage:
    python scripts/exit_study.py --gappers data/universe/gappers_2y.csv --before 2026-04-01
"""
from __future__ import annotations

import argparse
import csv
import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.core.types import Segment
from sgt.decision import validation as val
from sgt.decision.exit_study import EXITS, entry_signals, indicators, simulate
from sgt.decision.features import CostModelAdapter
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("exit_study")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gappers", type=Path, default=Path("data/universe/gappers_2y.csv"))
    ap.add_argument("--before", type=date.fromisoformat, required=True)
    ap.add_argument("--after", type=date.fromisoformat, default=date(1900, 1, 1))
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    ap.add_argument("--max-entries", type=int, default=3)
    ap.add_argument("--out", type=Path, default=Path("reports/decision/exit_study"))
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
            rth = np.array([b.segment is Segment.RTH for b in bars])
            if not rth.any():
                continue
            rth_idx = np.flatnonzero(rth)
            close_min = bars[rth_idx[-1]].minute_of_day + 1
            cutoff_i = int(max(i for i in rth_idx if bars[i].minute_of_day <= close_min - 5))
            ind = indicators(day)
            sigs = entry_signals(day, ind, rth, max_entry_minute=15 * 60)
            half = lambda i, px, _b=bars, _s=sym: 0.5 * cost.round_trip(_s, _b[i].ts, px)[0]  # noqa: E731
            for rule in EXITS:
                busy, taken = -1, 0
                for t in sigs:
                    if t <= busy or taken >= args.max_entries:
                        continue
                    tr = simulate(day, ind, int(t), rule, cutoff_i, half)
                    if tr is None:
                        continue
                    busy, taken = tr.exit_i, taken + 1
                    rows.append({"symbol": sym, "session_date": d.isoformat(), **asdict(tr)})
        if k % 200 == 0:
            log.info("%d/%d symbols", k, len(days))
    df = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out.with_suffix(".trades.csv"), index=False)
    lines = [f"# Exit study: same entries, {len(days)} symbols, sessions {args.after}..{args.before}", "",
             "| exit | trades | mean net | median | hit | avg win | avg loss | win/loss | top 5% share of gains | mean R | 90% CI (day-clustered) |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for rule in EXITS:
        x = df[df.exit_rule == rule]
        if x.empty:
            continue
        w, lo_ = x.net[x.net > 0], x.net[x.net <= 0]
        top = x.net.sort_values(ascending=False)
        share = top.head(max(1, len(x) // 20)).sum() / w.sum() if w.sum() > 0 else float("nan")
        ci = val.cluster_bootstrap_ci(x.net.to_numpy(), x.session_date.to_numpy(), alpha=0.10)
        lines.append(f"| {rule} | {len(x)} | {100 * x.net.mean():+.3f}% | {100 * x.net.median():+.3f}% | "
                     f"{(x.net > 0).mean():.1%} | {100 * w.mean():+.2f}% | {100 * lo_.mean():+.2f}% | "
                     f"{w.mean() / -lo_.mean():.2f} | {share:.0%} | {x.r_multiple.mean():+.3f} | "
                     f"[{100 * ci[0]:+.3f}%, {100 * ci[1]:+.3f}%] |")
    report = "\n".join(lines) + "\n"
    args.out.with_suffix(".report.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
