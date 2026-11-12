"""R3: skip shorts on 5-day price runners (docs/findings/prereg_price_runner_2026-11-12.md).

    .venv/bin/python scripts/price_runner_study.py      # run ONCE (frozen prereg); needs fetch_adjusted_daily first
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fade_repeat_days_study as rd  # noqa: E402
from trader_rules_long_study import boot_groups  # noqa: E402

BARS = Path("reports/adjusted_daily/bars.parquet")
OUT = Path("reports/price_runner")
ALPHA, MIN_TRAIN, MIN_HOLD, Q = 0.05, 100, 30, 0.75


def ret5(bars: pd.DataFrame, rows: pd.DataFrame) -> pd.Series:
    """Split-adjusted 5-session return ending at the last close strictly before each row's session; NaN if < 6."""
    by = {s: (g.date.to_numpy(), g.close.to_numpy(float)) for s, g in bars.sort_values(["symbol", "date"]).groupby("symbol")}
    out = []
    for sym, ses in zip(rows.symbol, rows.session):
        dates, closes = by.get(sym, (np.array([]), np.array([])))
        k = int(np.searchsorted(dates, ses))          # bars before the session: indices < k
        out.append(closes[k - 1] / closes[k - 6] - 1 if k >= 6 else np.nan)
    return pd.Series(out, index=rows.index, dtype=float)


def _p(x) -> float:
    x = np.asarray(x, float)
    return float(((x <= 0) | np.isnan(x)).mean())


def stats(f: pd.DataFrame, q: float) -> dict:
    f = f.dropna(subset=["net"])
    skip = f.ret5 > q                                   # NaN (unknown) compares False: kept
    kept, sk = f[~skip], f[skip]
    out = {"q75": q, "n_kept": len(kept), "n_skipped": len(sk), "kept": float(kept.net.mean()) if len(kept) else None,
           "skipped": float(sk.net.mean()) if len(sk) else None, "skipped_total": float(sk.net.sum())}
    if len(kept) and len(sk):
        dist = boot_groups(kept.session.to_numpy(), kept.net.to_numpy(float), sk.session.to_numpy(), sk.net.to_numpy(float))
        out.update(diff=out["kept"] - out["skipped"], p=_p(dist))
    return out


def passes(st: dict, minimum: int) -> bool:
    return (st["n_skipped"] >= minimum and (st.get("diff") or 0) > 0 and st.get("p", 1.0) < ALPHA
            and st["skipped_total"] <= 0)


def main() -> int:
    d = rd.mark_repeats(rd.load())
    d["ret5"] = ret5(pd.read_parquet(BARS), d)
    nn = d[d.news_own == 0]
    pop = nn[~nn.repeat]
    dev, hold = pop[pop.split == "dev"], pop[pop.split == "holdout"]
    q = float(dev.dropna(subset=["net"]).ret5.quantile(Q))
    train = stats(dev, q)
    out = {"prereg": "docs/findings/prereg_price_runner_2026-11-12.md", "train": train,
           "train_pass": passes(train, MIN_TRAIN), "holdout": None, "adopt": False,
           "secondary": {"all_nonews_train": stats(nn[nn.split == "dev"], q),
                         "share_runners_that_are_f8_repeats": float((nn.ret5 > q)[nn.split == "dev"].groupby(
                             nn[nn.split == "dev"].repeat).mean().get(True, np.nan))}}
    if out["train_pass"]:
        h = stats(hold, q)
        out["holdout"] = h
        out["adopt"] = passes(h, MIN_HOLD)
    OUT.mkdir(parents=True, exist_ok=True)
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
