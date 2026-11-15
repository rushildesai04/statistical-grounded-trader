"""Earnings skip and crowded-day cap for the daily model's long calls (docs/findings/prereg_long_rules_2026-11-15.md).

    .venv/bin/python scripts/long_rules_study.py                # training, run ONCE
    .venv/bin/python scripts/long_rules_study.py --holdout E    # only after rule E's training PASS, once (or V)

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.megacap import rules, score

DIRS = ("reports/megacap_daily_v2", "reports/megacap_oos", "reports/aaoi_daily")
EARNINGS = Path("data/earnings")
OUT = Path("reports/long_rules")
TRAIN, HOLD = ("2020-01-02", "2026-06-30"), ("2026-07-01", "2026-10-28")
ALPHA, SEED, N_BOOT = 0.05 / 2, 20261115, 10_000
MIN_SKIPPED = {"train": 30, "holdout": 8}
MIN_BINDS = {"train": 100, "holdout": 100}
MAX_BYTES = 5_000_000
COLS = ["session", "label", "pred", "position", "d_rv21"]


def load(root: Path = Path(".")) -> pd.DataFrame:
    """Every out-of-sample row of the 21 names; short calls hold nothing here (long calls only)."""
    parts = []
    for d in DIRS:
        for p in sorted((root / d).glob("oos_*.parquet")):
            parts.append(pd.read_parquet(p)[COLS].assign(symbol=p.stem.removeprefix("oos_")))
    t = pd.concat(parts, ignore_index=True).dropna(subset=["label", "pred", "position"])
    t["session"] = t.session.astype(str)
    t["position"] = t.position.where(t.position > 0, 0).astype(int)
    return t.sort_values(["session", "symbol"], kind="stable").reset_index(drop=True)


def load_earnings(symbols: list[str], root: Path = EARNINGS) -> dict[str, pd.Series]:
    out = {}
    for s in symbols:
        p = root / f"raw_{s}.parquet"
        if p.is_file() and p.stat().st_size <= MAX_BYTES:
            out[s] = pd.read_parquet(p, columns=["accepted"]).accepted
    return out


def books(t: pd.DataFrame, earnings: dict[str, pd.Series]) -> dict:
    """BASE, E and V per session over the whole table (each number uses only earlier sessions), plus the row flags."""
    w0 = rules.scaled(t, score.base_weight(t.d_rv21))
    flag = rules.earnings_flag(t, earnings) & (t.position > 0)
    factor = rules.cap_factor(rules.book_swing(t, w0))
    f_row = t.session.map(factor).fillna(1.0)
    return {"flag": flag, "factor": factor, "base": score.book(t, w0), "E": score.book(t, w0.where(~flag, 0.0)),
            "V": score.book(t, w0 * f_row)}


def boot(d: np.ndarray) -> tuple[float, float, float]:
    """(mean, one-sided 97.5% lower bound, p) of the per-session differences (one row per session) by session bootstrap; p = (1 + resamples
    <= 0) / (n + 1): fail closed."""
    if len(d) == 0:
        return float("nan"), float("nan"), 1.0
    idx = np.random.default_rng(SEED).integers(0, len(d), size=(N_BOOT, len(d)))
    boots = d[idx].mean(1)
    return float(d.mean()), float(np.quantile(boots, ALPHA)), float((1 + (boots <= 0).sum()) / (N_BOOT + 1))


def perf(r: pd.Series) -> dict:
    cum = (1 + r).cumprod()
    sd = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    return {"mean_day": float(r.mean()), "sd_day": sd, "sharpe": float(r.mean() / sd * np.sqrt(252)) if sd > 0 else None,
            "total": float(cum.iloc[-1] - 1), "max_dd": float((cum / cum.cummax() - 1).min()),
            "worst_day": float(r.min()), "worst_5": float(r.rolling(5).sum().min()) if len(r) >= 5 else None,
            "by_year": {y: float(v) for y, v in r.groupby(r.index.str[:4]).mean().items()}}


def _calls(c: pd.DataFrame) -> dict:
    if c.empty:
        return {"n": 0}
    worst = c.nsmallest(5, "net")
    return {"n": int(len(c)), "mean": float(c.net.mean()), "sd": float(c.net.std(ddof=1)) if len(c) > 1 else None,
            "win": float((c.net > 0).mean()),
            "worst": [[r.symbol, r.session, round(float(r.net), 4)] for r in worst.itertuples()]}


def evaluate(t: pd.DataFrame, b: dict, window: tuple[str, str], split: str) -> dict:
    inw = (t.session >= window[0]) & (t.session <= window[1])
    sessions = sorted(t.session[inw].unique())
    base, e, v = (b[k].reindex(sessions).fillna(0.0) for k in ("base", "E", "V"))
    calls = t[inw & (t.position > 0)].assign(net=lambda x: x.label - x.symbol.map(score.cost))
    skipped = calls[b["flag"].loc[calls.index]]
    kept = calls[~b["flag"].loc[calls.index]]
    out = {"split": split, "window": list(window), "sessions": len(sessions), "calls": int(len(calls)),
           "base": {**perf(base.ret), "gross": float(base.gross.mean())}}

    stat, lower, p = boot((e.ret - base.ret).to_numpy(float))
    enough = len(skipped) >= MIN_SKIPPED[split]
    out["E"] = {"skipped": _calls(skipped), "kept": _calls(kept), "mean_diff": stat, "lower": lower, "p": p,
                "book": {**perf(e.ret), "gross": float(e.gross.mean())},
                "pass": bool(enough and stat > 0 and p < ALPHA),
                "verdict": ("PASS" if enough and stat > 0 and p < ALPHA else
                            "FAIL" if enough else "NO VERDICT (too few skipped calls): FAIL")}

    fac = b["factor"].reindex(sessions)
    binds = int((fac < 1).sum())
    if v.gross.mean() <= 0:
        out["V"] = {"binds": binds, "pass": False, "verdict": "NO VERDICT (the capped book never trades): FAIL"}
        return out
    k = float(base.gross.mean() / v.gross.mean())
    stat, lower, p = boot((k * v.ret - base.ret).to_numpy(float))
    enough = binds >= MIN_BINDS[split]
    out["V"] = {"binds": binds, "avg_cut": float(1 - fac[fac < 1].mean()) if binds else 0.0, "k": k,
                "mean_diff": stat, "lower": lower, "p": p,
                "book_raw": {**perf(v.ret), "gross": float(v.gross.mean())},
                "book_same_money": {**perf(v.ret * k), "gross": float(v.gross.mean() * k)},
                "pass": bool(enough and stat > 0 and p < ALPHA),
                "verdict": ("PASS" if enough and stat > 0 and p < ALPHA else
                            "FAIL" if enough else "NO VERDICT (the cap rarely binds): FAIL")}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", choices=["E", "V"])
    a = ap.parse_args(argv)
    if a.holdout:                                           # fail closed: held-out only after that rule's training PASS
        p = OUT / "result_train.json"
        if not p.is_file() or not json.loads(p.read_text()).get(a.holdout, {}).get("pass"):
            print(f"rule {a.holdout} did not pass on training: the held-out slice stays closed")
            return 2
    t = load()
    b = books(t, load_earnings(sorted(t.symbol.unique())))
    window, split = (HOLD, "holdout") if a.holdout else (TRAIN, "train")
    res = {"prereg": "docs/findings/prereg_long_rules_2026-11-15.md", **evaluate(t, b, window, split)}
    if a.holdout:
        res = {k: v for k, v in res.items() if k not in ("E", "V") or k == a.holdout}
    OUT.mkdir(parents=True, exist_ok=True)
    name = f"result_{split}.json" if not a.holdout else f"result_holdout_{a.holdout}.json"
    (OUT / name).write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
