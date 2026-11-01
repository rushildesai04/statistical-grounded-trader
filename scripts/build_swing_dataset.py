"""Build the swing (daily-bar) decision dataset (sgt.swing.dataset).

Usage:
    python scripts/build_swing_dataset.py --dataset-id swing_v1
"""
from __future__ import annotations

import argparse
import logging

from sgt.brokers.costs import CALIBRATED_COST_MODEL
from sgt.decision.features import CostModelAdapter
from sgt.swing.dataset import build_swing_dataset


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--daily", default="data/daily")
    ap.add_argument("--out", default="data/decision")
    ap.add_argument("--dataset-id", required=True)
    ap.add_argument("--price-min", type=float, default=5.0)
    ap.add_argument("--dollar-vol-min", type=float, default=5e6)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    meta = build_swing_dataset(
        daily_dir=args.daily, out_dir=args.out, dataset_id=args.dataset_id,
        cost_source=CostModelAdapter(CALIBRATED_COST_MODEL, tag="calibrated"),
        price_min=args.price_min, dollar_vol_min=args.dollar_vol_min)
    print(f"rows={meta['rows']} symbols={meta['symbols']} sessions={len(meta['sessions'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
