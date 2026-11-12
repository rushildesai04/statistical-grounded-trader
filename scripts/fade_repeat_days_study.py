"""Skip fades on stocks the channel alerted in the prior 7 days (docs/findings/prereg_fade_repeat_days_2026-11-12.md).

    .venv/bin/python scripts/fade_repeat_days_study.py      # run ONCE (frozen prereg)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trader_rules_long_study import boot_groups  # noqa: E402  (seed 20261112, 10,000 resamples)

OUT = Path("reports/fade_repeat_days")
ALPHA, MIN_TRAIN, MIN_HOLD, WINDOW = 0.05, 100, 30, 7


def load() -> pd.DataFrame:
    d = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv").assign(split=n) for n in ("dev", "holdout")])
    return d.assign(session=d.session.astype(str))


def mark_repeats(d: pd.DataFrame, window: int = WINDOW) -> pd.DataFrame:
    """repeat = the same symbol has an event 1..window calendar days before this session (all events count)."""
    d = d.assign(day=pd.to_datetime(d.session)).sort_values(["symbol", "day"]).reset_index(drop=True)
    gap = (d.day - d.groupby("symbol").day.shift(1)).dt.days
    return d.assign(repeat=gap.between(1, window))


def _p(x: np.ndarray) -> float:
    x = np.asarray(x, float)
    return float(((x <= 0) | np.isnan(x)).mean())


def stats(b: pd.DataFrame) -> dict:
    b = b.dropna(subset=["net"])
    kept, sk = b[~b.repeat], b[b.repeat]
    out = {"n": len(b), "n_kept": len(kept), "n_skipped": len(sk), "all": float(b.net.mean()),
           "kept": float(kept.net.mean()) if len(kept) else None, "skipped": float(sk.net.mean()) if len(sk) else None,
           "skipped_total": float(sk.net.sum())}
    if len(kept) and len(sk):
        dist = boot_groups(kept.session.to_numpy(), kept.net.to_numpy(float), sk.session.to_numpy(),
                           sk.net.to_numpy(float))
        out.update(diff=out["kept"] - out["skipped"], p=_p(dist))
    return out


def train_pass(st: dict) -> bool:
    return (st["n_skipped"] >= MIN_TRAIN and (st.get("diff") or 0) > 0 and st.get("p", 1.0) < ALPHA
            and st["skipped_total"] <= 0)


def main() -> int:
    d = mark_repeats(load())
    nn = d[d.news_own == 0]
    dev, hold = nn[nn.split == "dev"], nn[nn.split == "holdout"]
    train = stats(dev)
    out = {"prereg": "docs/findings/prereg_fade_repeat_days_2026-11-12.md", "train_nonews": train,
           "train_pass": train_pass(train), "holdout_nonews": None, "adopt": False,
           "secondary": {"train_all": stats(d[d.split == "dev"]),
                         "train_nonews_1_3_days": stats(mark_repeats(load(), 3).query("news_own == 0 and split == 'dev'"))}}
    if out["train_pass"]:
        h = stats(hold)
        out["holdout_nonews"] = h
        out["adopt"] = h["n_skipped"] >= MIN_HOLD and (h.get("diff") or 0) > 0
    OUT.mkdir(parents=True, exist_ok=True)
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
