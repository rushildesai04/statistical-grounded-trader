"""Tested record of the daily model's calls per stock and side, for the site's Overview tab.

    .venv/bin/python scripts/build_call_stats.py     # rewrites config/models/call_stats.json from the OOS records

Source: the walk-forward out-of-sample records 2018-2026 (reports/megacap_daily_v2, megacap_oos, aaoi_daily), the same
records behind the Model Tape. Net = position x next close-to-close return - 4 bp (RDDT 10 bp). Descriptive: this is
the honest "how often did calls like this win" number, not a prediction."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

OUT = Path("config/models/call_stats.json")
DIRS = (Path("reports/megacap_daily_v2"), Path("reports/megacap_oos"), Path("reports/aaoi_daily"))
COST = {"RDDT": 0.0010}


def side_stats(x: pd.DataFrame, cost: float) -> dict:
    net = x.position * x.label - cost
    return {"n": int(len(x)), "win": round(float((net > 0).mean()), 4), "avg": round(float(net.mean()), 6)}


def main() -> int:
    out = {}
    for d in DIRS:
        for p in sorted(d.glob("oos_*.parquet")):
            sym = p.stem.removeprefix("oos_")
            t = pd.read_parquet(p)[["session", "label", "position"]].dropna()
            cost = COST.get(sym, 0.0004)
            out[sym] = {"from": str(t.session.min()), "to": str(t.session.max()), "days": int(len(t)),
                        "hold_avg": round(float(t.label.mean()), 6),
                        "long": side_stats(t[t.position > 0], cost), "short": side_stats(t[t.position < 0], cost)}
    OUT.write_text(json.dumps(dict(sorted(out.items())), indent=1) + "\n")
    print(len(out), "symbols ->", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
