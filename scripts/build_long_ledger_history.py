"""The long-calls ledger on the daily model's tested record, day by day (for `sgt.megacap.longonly.rarity`).

    .venv/bin/python scripts/build_long_ledger_history.py     # rewrites config/models/long_ledger_history.json

The same arithmetic as the live ledger (`sgt.megacap.longonly.ledger`): per name the next close-to-close move less the
cost when the stored walk-forward position is +1, else 0; the day is the mean over the names marked that day. Stored
out-of-sample predictions only (2018 to 2026-10); nothing is refitted. Descriptive: it says how often the tested
record had a stretch as weak as the live one, it decides nothing (the registered stop and date do)."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

DIRS = ("reports/megacap_daily_v2", "reports/megacap_oos", "reports/aaoi_daily")
OUT = Path("config/models/long_ledger_history.json")
COST = 0.0004                                         # the mark files' cost of a call (ret - net)


def days(frames: dict[str, pd.DataFrame], cost: float = COST) -> pd.Series:
    """session -> ledger return. ``frames``: symbol -> rows with ``session``, ``label`` (log move), ``position``."""
    t = pd.concat([f.assign(symbol=s) for s, f in frames.items()], ignore_index=True).dropna(subset=["label", "position"])
    t = t.assign(session=t.session.astype(str), net=np.where(t.position > 0, np.expm1(t.label) - cost, 0.0))
    return t.groupby("session").net.mean().sort_index()


def main() -> int:
    frames = {p.stem.removeprefix("oos_"): pd.read_parquet(p, columns=["session", "label", "position"])
              for d in DIRS for p in sorted(Path(d).glob("oos_*.parquet"))}
    if not frames:
        raise SystemExit("no stored predictions: nothing written")
    led = days(frames)
    OUT.write_text(json.dumps({"source": "scripts/build_long_ledger_history.py", "names": sorted(frames), "first": led.index[0],
                               "last": led.index[-1], "cost": COST, "days": [round(float(x), 6) for x in led]}) + "\n")
    print(f"{OUT}: {len(led)} sessions {led.index[0]} to {led.index[-1]}, {len(frames)} names, total {float((1 + led).prod() - 1):+.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
