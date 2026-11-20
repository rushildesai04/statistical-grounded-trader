"""Are day-after repeats the losing repeats? (docs/findings/prereg_fade_repeat_day1_2026-11-20.md)

    .venv/bin/python scripts/fade_repeat_day1_study.py      # run ONCE (frozen prereg)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fade_repeat_days_study import load  # noqa: E402
from trader_rules_long_study import boot_groups  # noqa: E402  (seed 20261112, 10,000 resamples)

OUT = Path("reports/fade_repeat_day1")
ALPHA, MIN_N, WINDOW = 0.05, 30, 7


def gaps(d: pd.DataFrame) -> pd.DataFrame:
    """gap = calendar days since the same symbol's previous event (the registered repeat test's definition)."""
    d = d.assign(day=pd.to_datetime(d.session)).sort_values(["symbol", "day"]).reset_index(drop=True)
    return d.assign(gap=(d.day - d.groupby("symbol").day.shift(1)).dt.days)


def stats(b: pd.DataFrame) -> dict:
    """Day-after repeats (gap 1) against later repeats (gap 2..WINDOW) of the fades in ``b``."""
    b = b.dropna(subset=["net"])
    one, later = b[b.gap == 1], b[b.gap.between(2, WINDOW)]
    out = {"n_day_after": len(one), "n_later": len(later),
           "day_after": float(one.net.mean()) if len(one) else None, "later": float(later.net.mean()) if len(later) else None,
           "day_after_wins": float((one.net > 0).mean()) if len(one) else None}
    if len(one) and len(later):
        dist = boot_groups(one.session.to_numpy(), one.net.to_numpy(float), later.session.to_numpy(), later.net.to_numpy(float))
        dist = np.asarray(dist, float)
        out.update(diff=out["day_after"] - out["later"], p=float(((dist <= 0) | np.isnan(dist)).mean()))
    return out


def passed(st: dict) -> bool:
    return bool(st["n_day_after"] >= MIN_N and (st.get("day_after") or 0) > 0 and (st.get("diff") or 0) > 0
                and st.get("p", 1.0) < ALPHA)


def main() -> int:
    res = OUT / "result.json"
    if res.is_file():
        print(f"{res} exists: the run was made once and is not repeated")
        return 2
    d = gaps(load())
    nn = d[d.news_own == 0]
    hold = stats(nn[nn.split == "holdout"])
    out = {"prereg": "docs/findings/prereg_fade_repeat_day1_2026-11-20.md", "holdout_nonews": hold, "pass": passed(hold),
           "training_disclosed": stats(nn[nn.split == "dev"])}
    OUT.mkdir(parents=True, exist_ok=True)
    res.write_text(json.dumps(out, indent=2, default=str) + "\n")
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
