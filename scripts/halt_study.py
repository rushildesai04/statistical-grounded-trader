"""LULD halt-resumption trades on US stocks: fetch halt history, build events, develop, score ONCE.

Rules: docs/findings/prereg_halts_2026-11-03.md (written before any price around a halt is read).

    python scripts/halt_study.py fetch     # Nasdaq Trader halt history per session (no prices), resumable
"""
from __future__ import annotations

import argparse
import time
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from megacap_study import adjusted_daily
from sgt.catalysts.halts import HALTS_URL, MAX_PAYLOAD_BYTES, parse_halt_rows
from sgt.core.http import get_bounded

START, END = date(2018, 1, 2), date(2026, 10, 29)
RAW = Path("data/halts/raw")
PAUSE = 1.0                                     # seconds between feed requests (polite)


def sessions() -> list[date]:
    idx = pd.to_datetime(adjusted_daily("SPY").index)
    return [d.date() for d in idx if START <= d.date() <= END]


def cmd_fetch() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    todo = [d for d in sessions() if not (RAW / f"{d:%Y%m%d}.parquet").exists()]
    print(f"{len(todo)} sessions to fetch", flush=True)
    errors = 0
    with httpx.Client(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False,
                      headers={"User-Agent": "statistical-grounded-trader research"}) as c:
        for i, d in enumerate(todo):
            try:
                r = get_bounded(c, HALTS_URL, max_bytes=MAX_PAYLOAD_BYTES, params={"haltdate": f"{d:%m%d%Y}"})
                if r.status_code != 200:
                    raise RuntimeError(f"HTTP {r.status_code}")
                rows = parse_halt_rows(r.content)
                pd.DataFrame(rows, columns=["symbol", "market", "reason", "halt", "resume"]).to_parquet(
                    RAW / f"{d:%Y%m%d}.parquet")
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                errors += 1
                print(f"{d}: {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            if i % 100 == 0:
                print(f"{i}/{len(todo)} {d}", flush=True)
            time.sleep(PAUSE)
    print(f"done, errors {errors}", flush=True)
    return 1 if errors else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch"])
    args = ap.parse_args()
    return {"fetch": cmd_fetch}[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
