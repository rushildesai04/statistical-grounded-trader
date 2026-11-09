"""Expected-value sizing for no-news fades (prereg_fade_evsize_2026-11-09).

    python scripts/fade_ev_study.py fit       # once: fit on training, freeze config/models/fade_ev_v1.json, report

Additive ridge (alpha fixed in the prereg) on window x price band x volatility group, real-stop nets, no-news only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ALPHA, LO, HI, SEED, N_BOOT = 200.0, 0.25, 2.0, 20261109, 10_000
CALM, JUMPY = 0.015072, 0.029200
MODEL = Path(__file__).resolve().parents[1] / "config" / "models" / "fade_ev_v1.json"
ROOT = Path("/Users/rushil/code/statistical-grounded-trader")      # data + reports live in the main checkout
LEVELS = {"window": ["pre11", "11-14", "14-16"], "band": ["$1-2", "$2-5", "$5-10", "$10-50"],
          "vol": ["calm", "mid", "jumpy"]}


def groups(entry_ts: pd.Series, entry: pd.Series, sigma: pd.Series) -> pd.DataFrame:
    et = pd.to_datetime(entry_ts, utc=True).dt.tz_convert("America/New_York")
    m = et.dt.hour * 60 + et.dt.minute
    window = np.where(m < 11 * 60, "pre11", np.where(m < 14 * 60, "11-14", "14-16"))
    band = pd.cut(entry.astype(float), [0, 2, 5, 10, 1e9], labels=LEVELS["band"], right=False).astype(str)
    s = sigma.astype(float)
    vol = np.where(s.isna(), "mid", np.where(s < CALM, "calm", np.where(s >= JUMPY, "jumpy", "mid")))
    return pd.DataFrame({"window": window, "band": band, "vol": vol}, index=entry.index)


def design(g: pd.DataFrame) -> np.ndarray:
    cols = [np.ones(len(g))]
    for k, lv in LEVELS.items():
        for v in lv[1:]:                                   # drop-one coding; the first level is the base
            cols.append((g[k].to_numpy() == v).astype(float))
    return np.column_stack(cols)


def fit_ridge(X: np.ndarray, y: np.ndarray, alpha: float) -> np.ndarray:
    pen = alpha * np.eye(X.shape[1])
    pen[0, 0] = 0.0                                        # never shrink the intercept
    return np.linalg.solve(X.T @ X + pen, X.T @ y)


def weights(ev: np.ndarray, mean_ev: float) -> np.ndarray:
    return np.clip(ev / mean_ev, LO, HI)


def predict(model: dict, g: pd.DataFrame) -> np.ndarray:
    return design(g) @ np.array(model["coef"])


def load(split: str) -> pd.DataFrame:
    root = ROOT
    e = pd.read_csv(root / "reports" / "fade_news" / f"{split}_events.csv")
    src = pd.read_csv(root / "reports" / "telegram" / "trades_pooled_first.csv")[["symbol", "session", "entry_ts", "entry"]]
    e = e.merge(src, on=["symbol", "session"]).dropna(subset=["net", "entry_ts"])
    e["session"] = e.session.astype(str)
    q = pd.read_csv(root / "reports" / "fade_stops" / "fills.csv")
    q["session"] = q.session.astype(str)
    q["adj"] = (q.fill.where(q.fill.notna(), q.model_cover) - q.model_cover) / q.entry
    e = e.merge(q[["symbol", "session", "adj"]], on=["symbol", "session"], how="left").fillna({"adj": 0.0})
    e["net_real"] = e.net - e.adj
    cache = Path(sys.argv[2]) / f"vol_{split}.csv" if len(sys.argv) > 2 else None
    if cache is not None and cache.exists():             # same sigma as fade_volsize_study, computed earlier
        vol = pd.read_csv(cache)[["symbol", "session", "sigma"]].assign(session=lambda d: d.session.astype(str))
        e = e.merge(vol, on=["symbol", "session"], how="left")
    else:
        from fade_volsize_study import sigma
        from sgt.intraday.store import ParquetBarStore
        store = ParquetBarStore(str(ROOT / "data" / "alpaca"))
        e["sigma"] = [sigma(store, r.symbol, r.session, pd.Timestamp(r.entry_ts)) for r in e.itertuples()]
    return e[~((e.alert_type == "news") | (e.news_own == 1))].reset_index(drop=True)


def session_diff(t: pd.DataFrame, w: np.ndarray) -> tuple[float, float]:
    """Mean per-session P&L difference (weighted - flat) in % of one flat trade, with a bootstrap lower bound."""
    u, inv = np.unique(t.session.to_numpy(), return_inverse=True)
    d = np.bincount(inv, t.net_real.to_numpy() * (w - 1.0), len(u))
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    means = d[b].mean(1)
    return float(d.mean()), float(np.quantile(means, 0.05))


def cmd_fit() -> int:
    tr, ho = load("dev"), load("holdout")
    gtr, gho = groups(tr.entry_ts, tr.entry, tr.sigma), groups(ho.entry_ts, ho.entry, ho.sigma)
    coef = fit_ridge(design(gtr), tr.net_real.to_numpy(float), ALPHA)
    ev_tr = design(gtr) @ coef
    mean_ev = float(ev_tr.mean())
    w_tr = weights(ev_tr, mean_ev)
    scale = float(1.0 / w_tr.mean())
    model = {"version": "fade_ev_v1", "prereg": "docs/findings/prereg_fade_evsize_2026-11-09.md", "alpha": ALPHA,
             "levels": LEVELS, "coef": [float(c) for c in coef], "mean_ev": mean_ev, "scale": scale,
             "lo": LO, "hi": HI, "calm": CALM, "jumpy": JUMPY, "n_train": len(tr)}
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(model, indent=2))
    names = ["intercept"] + [f"{k}={v}" for k, lv in LEVELS.items() for v in lv[1:]]
    print("coefficients (net per trade, vs base pre11 / $1-2 / calm):")
    for n, c in zip(names, coef):
        print(f"  {n:14s} {c:+.2%}")
    for name, t, g in (("training (in-sample)", tr, gtr), ("held-out (descriptive)", ho, gho)):
        w = weights(design(g) @ coef, mean_ev) * scale
        diff, lb = session_diff(t, w)
        print(f"{name}: n {len(t)} flat {t.net_real.mean():+.2%}/trade, EV-sized {(t.net_real * w).mean():+.2%}/trade "
              f"(mean size {w.mean():.2f}x); per session {diff:+.2%} of a trade [5% lb {lb:+.2%}]")
        cell = t.assign(w=w, **g).groupby(["window", "vol"]).agg(n=("net_real", "size"), net=("net_real", "mean"),
                                                                   w=("w", "mean"))
        if name.startswith("held"):
            print(cell.round(3).to_string())
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["fit"])
    ap.add_argument("volcache", nargs="?")
    a = ap.parse_args()
    return {"fit": cmd_fit}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
