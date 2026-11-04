"""Download Coinbase 1-minute candles for the crypto program (training months only until a vault unlock).

    python scripts/crypto_fetch.py --study crypto-leadlag-2026-11          # all products, the study's training months
"""
from __future__ import annotations

import argparse
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from sgt.crypto import coinbase
from sgt.research.vault import load_registry

ROOT = Path("data/crypto/coinbase")


def train_months(study: str) -> list[str]:
    s = load_registry()[study]
    lo, hi = (date.fromisoformat(str(x)) for x in s.train)
    return [p.strftime("%Y-%m") for p in pd.period_range(lo, hi, freq="M")]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study", required=True)
    ap.add_argument("--products", nargs="*", default=[coinbase.LEADER, *coinbase.ALTS])
    args = ap.parse_args()
    months = train_months(args.study)
    with coinbase.client() as c:
        for prod in args.products:
            for m in months:
                p = coinbase.fetch_month(c, ROOT, prod, m)
                if p is not None:
                    logging.info("%s %s: %d candles", prod, m, len(pd.read_parquet(p)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
