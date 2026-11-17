"""The daily model's long calls as a market timer (docs/findings/prereg_dip_timer_2026-11-17.md).

    .venv/bin/python scripts/dip_timer_study.py              # training, run ONCE
    .venv/bin/python scripts/dip_timer_study.py --holdout    # only after the training PASS, once

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import long_rules_study as base

OUT = Path("reports/dip_timer")
TRAIN, HOLD = base.TRAIN, base.HOLD
Q, MIN_HIST = 0.80, 250
COST = 0.0001                                   # round trip of the market position
ALPHA, SEED, N_BOOT = 0.05, 20261117, 10_000
MIN_SIGNAL = {"train": 150, "holdout": 5}


def breadth(t: pd.DataFrame) -> pd.Series:
    """Per session: long calls made that day / names with a row that day."""
    return (t.position > 0).groupby(t.session).mean().sort_index()


def signal(b: pd.Series, q: float = Q, min_hist: int = MIN_HIST) -> pd.Series:
    """True on a session whose breadth is at or above the q-th percentile of ALL EARLIER sessions (at least
    ``min_hist`` of them) and has at least one call. The session itself and later ones never enter its cut."""
    cut = b.expanding(min_periods=min_hist).quantile(q).shift(1)
    return (b >= cut) & (b > 0) & cut.notna()


def dips(same_day: pd.Series, q: float = Q, min_hist: int = MIN_HIST) -> pd.Series:
    """The naive comparison: sessions whose own market return is at or below the (1-q)-th percentile of all earlier
    sessions' returns (the largest one-day falls, with the same causal cut)."""
    cut = same_day.expanding(min_periods=min_hist).quantile(1 - q).shift(1)
    return (same_day <= cut) & cut.notna()


def closes(sym: str) -> pd.Series:
    from megacap_study import adjusted_daily

    d = adjusted_daily(sym)
    c = pd.Series(d.close.to_numpy(float), index=[str(x)[:10] for x in d.index]).sort_index()
    if c.index.duplicated().any() or not np.isfinite(c).all() or not (c > 0).all():
        raise SystemExit(f"{sym}: closes duplicated, missing or not positive")
    return c


def frame(t: pd.DataFrame, close: pd.Series, last: str) -> pd.DataFrame:
    """One row per decision session up to ``last``: breadth, signal, the market's own return that day (for the naive
    rule) and its NEXT close-to-close return (what a position taken at that close earns)."""
    t = t[t.session <= last]
    b = breadth(t)
    c = close[close.index <= pd.Series(close.index[close.index > last]).min()] if (close.index > last).any() else close
    f = pd.DataFrame({"breadth": b, "signal": signal(b)})
    f["same"] = (c / c.shift(1) - 1).reindex(f.index)
    f["next"] = (c.shift(-1) / c - 1).reindex(f.index)
    f["dip"] = dips(f.same)
    f["names_next"] = t.groupby("session").label.mean().reindex(f.index)
    return f.dropna(subset=["next"])


def boot(f: pd.DataFrame, stat) -> np.ndarray:
    """``stat(frame)`` on N_BOOT resamples of whole calendar months; a resample where it is undefined counts as -inf
    (fail closed)."""
    months = f.index.str[:7].to_numpy()
    groups = [np.flatnonzero(months == m) for m in np.unique(months)]
    rng = np.random.default_rng(SEED)
    out = np.empty(N_BOOT)
    for i in range(N_BOOT):
        idx = np.concatenate([groups[j] for j in rng.integers(0, len(groups), len(groups))])
        v = stat(f.iloc[idx])
        out[i] = v if np.isfinite(v) else -np.inf
    return out


def diff(f: pd.DataFrame, col: str = "signal") -> float:
    a, b = f.next[f[col]], f.next[~f[col]]
    return float(a.mean() - b.mean()) if len(a) and len(b) else float("nan")


def signal_net(f: pd.DataFrame) -> float:
    a = f.next[f.signal]
    return float(a.mean() - COST) if len(a) else float("nan")


def evaluate(f: pd.DataFrame, window: tuple[str, str], split: str) -> dict:
    f = f[(f.index >= window[0]) & (f.index <= window[1])]
    s, o = f.next[f.signal], f.next[~f.signal]
    d, net = diff(f), signal_net(f)
    out = {"split": split, "window": list(window), "sessions": int(len(f)), "signal_days": int(len(s)),
           "signal_share": float(len(s) / len(f)) if len(f) else None, "diff": d, "signal_mean_net": net,
           "signal": {"mean": float(s.mean()) if len(s) else None, "win": float((s > 0).mean()) if len(s) else None,
                      "sd": float(s.std(ddof=1)) if len(s) > 1 else None},
           "other": {"n": int(len(o)), "mean": float(o.mean()) if len(o) else None,
                     "win": float((o > 0).mean()) if len(o) else None}}
    timer = (f.next - COST).where(f.signal, 0.0)
    out["timer"] = base.perf(timer)
    out["holding"] = base.perf(f.next)
    out["by_year"] = {y: {"signal_days": int(g.signal.sum()), "signal_mean": float(g.next[g.signal].mean()) if g.signal.any() else None,
                          "other_mean": float(g.next[~g.signal].mean())} for y, g in f.groupby(f.index.str[:4])}
    out["naive_dip"] = {"days": int(f.dip.sum()), "mean_next": float(f.next[f.dip].mean()) if f.dip.any() else None,
                        "overlap_with_signal": int((f.dip & f.signal).sum()),
                        "signal_not_dip_mean": float(f.next[f.signal & ~f.dip].mean()) if (f.signal & ~f.dip).any() else None,
                        "dip_not_signal_mean": float(f.next[f.dip & ~f.signal].mean()) if (f.dip & ~f.signal).any() else None}
    out["names_next_on_signal_days"] = float(f.names_next[f.signal].mean()) if f.signal.any() else None
    if split == "train":
        enough = len(s) >= MIN_SIGNAL[split]
        if enough:
            bd, bn = boot(f, diff), boot(f, signal_net)
            p = float((1 + (bd <= 0).sum()) / (N_BOOT + 1))
            lower = float(np.quantile(bn, ALPHA))
        else:
            p, lower = 1.0, float("nan")
        ok = bool(enough and d > 0 and p < ALPHA and net > 0 and lower > 0)
        out.update(p=p, signal_net_lower=lower, alpha=ALPHA, **{"pass": ok},
                   verdict="PASS" if ok else "FAIL" if enough else "NO VERDICT (too few signal days): FAIL")
    else:
        ok = bool(len(s) >= MIN_SIGNAL[split] and d > 0 and net > 0)
        out.update(consistent=ok, verdict="CONSISTENT" if ok else "NOT CONSISTENT")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args(argv)
    train = OUT / "result_train.json"
    if a.holdout:                                           # fail closed: held-out only after the training PASS
        if not train.is_file() or not json.loads(train.read_text()).get("pass"):
            print("the training run did not pass: the held-out slice stays closed")
            return 2
        if (OUT / "result_holdout.json").is_file():
            print("the held-out run was made once and is not repeated")
            return 2
    elif train.is_file():
        print(f"{train} exists: the training run was made once and is not repeated")
        return 2
    window, split = (HOLD, "holdout") if a.holdout else (TRAIN, "train")
    t = base.load()
    res = {"prereg": "docs/findings/prereg_dip_timer_2026-11-17.md"}
    for name, sym in (("SPY", "SPY"), ("QQQ", "QQQ")):
        res[name] = evaluate(frame(t, closes(sym), window[1]), window, split)
    res["verdict"] = res["SPY"]["verdict"]                  # SPY decides; QQQ is reported
    res["pass" if split == "train" else "consistent"] = res["SPY"].get("pass", res["SPY"].get("consistent"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"result_{split}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
