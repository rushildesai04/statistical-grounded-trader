"""Readers of forward-record ledgers for the nightly summary. Pure over a CSV file; never raises."""
from __future__ import annotations

import csv
import logging
import math
from pathlib import Path

log = logging.getLogger(__name__)
NOISE_LEDGER = Path("reports/noise_area_forward/ledger.csv")
MAX_ROWS = 5_000                    # two instruments x a few years of sessions


def noise_area_line(path: Path = NOISE_LEDGER, decide_at: int = 250) -> str | None:
    """The intraday trend rule's forward record (owner 2026-11-19: record only, no trades): per instrument the
    sessions recorded, the sum and the average of the daily net return. None before the first session."""
    try:
        if not path.is_file():
            return None
        by: dict[str, list[float]] = {}
        with path.open(newline="") as fh:
            for i, r in enumerate(csv.DictReader(fh)):
                if i >= MAX_ROWS:
                    break
                try:
                    x = float(r["ret"])
                except (KeyError, TypeError, ValueError):
                    continue
                if math.isfinite(x):
                    by.setdefault(str(r.get("symbol") or "")[:8], []).append(x)
        if not by:
            return None
        bits = [f"{s} {len(v)} sessions, sum {sum(v):+.2%}, avg {sum(v) / len(v):+.3%}/day" for s, v in sorted(by.items())]
        return "trend rule (record only, no trades): " + " · ".join(bits) + f"; decided at {decide_at} sessions"
    except Exception:  # noqa: BLE001 - an informational line
        log.exception("noise-area forward line failed")
        return None
