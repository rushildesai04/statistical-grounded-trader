"""Money score for the daily model's calls (docs/findings/prereg_money_score_2026-11-14.md).

    .venv/bin/python scripts/money_score_study.py              # training, run ONCE
    .venv/bin/python scripts/money_score_study.py --holdout    # only after a training PASS, once

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.megacap import score

DIRS = ("reports/megacap_daily_v2", "reports/megacap_oos", "reports/aaoi_daily")
OUT = Path("reports/money_score")
TRAIN, HOLD = ("2020-01-02", "2026-06-30"), ("2026-07-01", "2026-10-28")
ALPHA, SEED, N_BOOT = 0.05 / 2, 20261114, 10_000
MIN_CALLS = {"train": 300, "holdout": 40}
COLS = ["session", "label", "pred", "position", "d_rv21"]


def load(root: Path = Path(".")) -> pd.DataFrame:
    parts = []
    for d in DIRS:
        for p in sorted((root / d).glob("oos_*.parquet")):
            parts.append(pd.read_parquet(p)[COLS].assign(symbol=p.stem.removeprefix("oos_")))
    t = pd.concat(parts, ignore_index=True).dropna(subset=["label", "pred", "position"])
    t["session"] = t.session.astype(str)
    return t.sort_values(["session", "symbol"], kind="stable").reset_index(drop=True)


def scored(t: pd.DataFrame) -> pd.DataFrame:
    e = score.expected(t, score.calibration(t))
    ref = score.reference(e)
    e["z_ref"] = e.month.map(ref)
    e["m"] = score.multiplier(e, ref)
    e["net"] = e.position * e.label - e.symbol.map(score.cost)
    return e


def boot_p(sessions: np.ndarray, num: np.ndarray, den: np.ndarray | None = None) -> tuple[float, float, float]:
    """(statistic, one-sided 97.5% lower bound, p) of sum(num)/sum(den) by session-clustered bootstrap; den None =
    the mean over sessions. p = (1 + resamples <= 0) / (n + 1): fail closed."""
    u, inv = np.unique(sessions, return_inverse=True)
    a = np.bincount(inv, num, len(u))
    b = np.ones(len(u)) if den is None else np.bincount(inv, den, len(u))
    idx = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        boots = a[idx].sum(1) / b[idx].sum(1)
        stat = a.sum() / b.sum()
    boots = np.where(np.isfinite(boots), boots, -np.inf)                  # an empty resample counts against
    return float(stat), float(np.quantile(boots, ALPHA)), float((1 + (boots <= 0).sum()) / (N_BOOT + 1))


def t1(c: pd.DataFrame) -> dict:
    """Mean net of calls with z above z_ref minus mean net of the rest (ev <= 0 counts as below)."""
    hi = ((c.ev > 0) & (c.z > c.z_ref)).to_numpy()
    if hi.sum() == 0 or (~hi).sum() == 0:
        return {"n_hi": int(hi.sum()), "n_lo": int((~hi).sum()), "pass": False}
    u, inv = np.unique(c.session.to_numpy(), return_inverse=True)
    net = c.net.to_numpy(float)
    sh, nh = np.bincount(inv, net * hi, len(u)), np.bincount(inv, hi.astype(float), len(u))
    sl, nl = np.bincount(inv, net * ~hi, len(u)), np.bincount(inv, (~hi).astype(float), len(u))
    idx = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        boots = sh[idx].sum(1) / nh[idx].sum(1) - sl[idx].sum(1) / nl[idx].sum(1)
    boots = np.where(np.isfinite(boots), boots, -np.inf)
    diff = float(net[hi].mean() - net[~hi].mean())
    p = float((1 + (boots <= 0).sum()) / (N_BOOT + 1))
    return {"n_hi": int(hi.sum()), "n_lo": int((~hi).sum()), "mean_hi": float(net[hi].mean()),
            "mean_lo": float(net[~hi].mean()), "diff": diff, "lower": float(np.quantile(boots, ALPHA)), "p": p,
            "pass": bool(diff > 0 and p < ALPHA)}


def _perf(r: pd.Series) -> dict:
    cum = (1 + r).cumprod()
    sd = float(r.std(ddof=1))
    return {"mean_day": float(r.mean()), "sharpe": float(r.mean() / sd * np.sqrt(252)) if sd > 0 else None,
            "total": float(cum.iloc[-1] - 1), "max_dd": float((cum / cum.cummax() - 1).min()),
            "by_year": {y: float(v) for y, v in r.groupby(r.index.str[:4]).mean().items()}}


def t2(e: pd.DataFrame, sessions: list[str]) -> dict:
    """Paired per session: k x SCORE - BASE at equal average gross."""
    w0 = score.base_weight(e.d_rv21)
    base = score.book(e, w0).reindex(sessions).fillna(0.0)
    sc = score.book(e, w0 * e.m).reindex(sessions).fillna(0.0)
    if sc.gross.mean() <= 0:
        return {"pass": False, "note": "SCORE never trades"}
    k = float(base.gross.mean() / sc.gross.mean())
    d = (k * sc.ret - base.ret).to_numpy(float)
    stat, lower, p = boot_p(np.asarray(sessions), d)
    return {"k": k, "mean_diff": stat, "lower": lower, "p": p, "pass": bool(stat > 0 and p < ALPHA),
            "base": {**_perf(base.ret), "gross": float(base.gross.mean())},
            "score": {**_perf(sc.ret * k), "gross": float(sc.gross.mean() * k)}}


def evaluate(e: pd.DataFrame, window: tuple[str, str], split: str, sides: tuple[int, ...]) -> dict:
    w = e[(e.session >= window[0]) & (e.session <= window[1])]
    sessions = sorted(w.session.unique())
    calls = w[w.position.isin(sides)]                                       # calls of the tested sides only
    book_rows = w.assign(position=w.position.where(w.position.isin(sides), 0))
    out = {"split": split, "sides": list(sides), "sessions": len(sessions), "calls": int(len(calls)),
           "ev_nonpositive_share": float((calls.ev <= 0).mean()) if len(calls) else None,
           "mean_net_per_call": float(calls.net.mean()) if len(calls) else None}
    if len(calls) < MIN_CALLS[split]:
        return {**out, "verdict": "NO VERDICT (too few calls): FAIL"}
    out["T1"], out["T2"] = t1(calls), t2(book_rows, sessions)
    out["verdict"] = ("PASS" if out["T1"]["pass"] and out["T2"]["pass"] else
                      "T1 only" if out["T1"]["pass"] else "FAIL")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    e = scored(load())
    window, split = (HOLD, "holdout") if a.holdout else (TRAIN, "train")
    b = score.calibration(e)
    res = {"prereg": "docs/findings/prereg_money_score_2026-11-14.md", "window": window,
           "long": evaluate(e, window, split, (1,)), "long_short": evaluate(e, window, split, (1, -1)),
           "b_by_year": {y: float(np.mean([v for m, v in b.items() if m.startswith(y)])) for y in
                         sorted({m[:4] for m in b})},
           "z_ref_last": float(e.z_ref.dropna().iloc[-1]) if e.z_ref.notna().any() else None}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"result_{split}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
