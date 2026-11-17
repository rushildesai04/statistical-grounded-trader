"""Market filter for the daily model's long calls (docs/findings/prereg_long_market_filter_2026-11-16.md).

    .venv/bin/python scripts/long_market_filter_study.py              # training, run ONCE
    .venv/bin/python scripts/long_market_filter_study.py --holdout    # only after the training PASS, once

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import long_rules_study as base
from sgt.megacap import rules, score

MARKET = Path("data/daily/SPY.parquet")
OUT = Path("reports/long_market_filter")
TRAIN, HOLD = base.TRAIN, base.HOLD
ALPHA, SEED, N_BOOT = 0.05 / 3, 20261116, 10_000
MIN_BELOW = {"train": 100, "holdout": 20}
MAX_BYTES = 5_000_000


def load_market(path: Path = MARKET) -> pd.Series:
    """SPY closes by ISO session date (the chart price). Refuses a file that is missing, oversized or has a bad close."""
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise SystemExit(f"market file {path} missing or too large")
    d = pd.read_parquet(path, columns=["date", "close"])
    c = pd.Series(d.close.to_numpy(float), index=d.date.astype(str).str[:10])
    if c.index.duplicated().any() or not np.isfinite(c).all() or not (c > 0).all():
        raise SystemExit("market closes are duplicated, missing or not positive")
    return c.sort_index()


def books(t: pd.DataFrame, market: pd.Series) -> dict:
    """BASE and M per session, and the rule's flag per session (each flag uses only closes before its session)."""
    return books_with(t, rules.below_trend(sorted(t.session.unique()), market))


def books_with(t: pd.DataFrame, below: pd.Series) -> dict:
    w0 = rules.scaled(t, score.base_weight(t.d_rv21))
    row = t.session.map(below).fillna(False).astype(bool)
    return {"below": below, "base": score.book(t, w0), "M": score.book(t, w0.where(~row, 0.0))}


def boot(d: np.ndarray) -> tuple[float, float, float]:
    """(mean, one-sided lower bound at ALPHA, p) of the per-session differences by session bootstrap;
    p = (1 + resamples <= 0) / (n + 1): fail closed."""
    if len(d) == 0:
        return float("nan"), float("nan"), 1.0
    idx = np.random.default_rng(SEED).integers(0, len(d), size=(N_BOOT, len(d)))
    boots = d[idx].mean(1)
    return float(d.mean()), float(np.quantile(boots, ALPHA)), float((1 + (boots <= 0).sum()) / (N_BOOT + 1))


def evaluate(t: pd.DataFrame, b: dict, window: tuple[str, str], split: str) -> dict:
    inw = (t.session >= window[0]) & (t.session <= window[1])
    sessions = sorted(t.session[inw].unique())
    bs, m = (b[k].reindex(sessions).fillna(0.0) for k in ("base", "M"))
    below = b["below"].reindex(sessions).fillna(False).astype(bool)
    calls = t[inw & (t.position > 0)].assign(net=lambda x: x.label - x.symbol.map(score.cost))
    cut = calls.session.map(below).astype(bool)
    skipped, kept = calls[cut], calls[~cut]
    n_below = int(below.sum())
    n_with_call = int(len(set(skipped.session)))
    stat, lower, p = boot((m.ret - bs.ret).to_numpy(float))
    enough = n_with_call >= MIN_BELOW[split]
    ok = bool(enough and stat > 0 and p < ALPHA)
    return {"split": split, "window": list(window), "sessions": len(sessions), "calls": int(len(calls)),
            "alpha": ALPHA, "below_sessions": n_below, "below_sessions_with_a_call": n_with_call,
            "below_by_year": {y: int(v) for y, v in below.groupby(below.index.str[:4]).sum().items()},
            "base_mean_day_below": float(bs.ret[below].mean()) if n_below else None,
            "base_mean_day_other": float(bs.ret[~below].mean()) if n_below < len(sessions) else None,
            "skipped": base._calls(skipped), "kept": base._calls(kept),
            "base": {**base.perf(bs.ret), "gross": float(bs.gross.mean())},
            "M": {**base.perf(m.ret), "gross": float(m.gross.mean())},
            "mean_diff": stat, "lower": lower, "p": p, "pass": ok,
            "verdict": "PASS" if ok else "FAIL" if enough else "NO VERDICT (too few below-trend sessions): FAIL"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args(argv)
    train = OUT / "result_train.json"
    if a.holdout:                                           # fail closed: held-out only after the training PASS
        if not train.is_file() or not json.loads(train.read_text()).get("pass"):
            print("rule M did not pass on training: the held-out slice stays closed")
            return 2
    elif train.is_file():                                   # the training run is a single run
        print(f"{train} exists: the training run was made once and is not repeated")
        return 2
    t = base.load()
    b = books(t, load_market())
    window, split = (HOLD, "holdout") if a.holdout else (TRAIN, "train")
    res = {"prereg": "docs/findings/prereg_long_market_filter_2026-11-16.md", **evaluate(t, b, window, split)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"result_{split}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
