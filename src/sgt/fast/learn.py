"""Fast learned large-cap books, 30 minutes to the close (prereg_fast_megacap_2026-11-03).

Rows reuse the frozen brains features (``sgt.megacap.features.session_rows``, point in time) and add
labels for several horizons on non-overlapping decision grids plus ``slot_ret`` (same-window mean
return over the previous 20 sessions). Models: the brains LightGBM settings, monthly expanding
walk-forward; per-name models with three thresholds, and one pooled model per horizon for the
cross-sectional long-2 / short-2 book.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.megacap.features import DECISIONS, FEATURES, SLOT_LOOKBACK, session_rows, to_grid
from sgt.megacap.walkforward import LGB_PARAMS, MIN_TRAIN_SESSIONS

HORIZONS: dict[str, int | None] = {"30": 30, "60": 60, "120": 120, "close": None}
DECISIONS_H = {"30": tuple(range(30, 361, 30)), "60": tuple(range(30, 331, 60)), "120": (30, 150, 270),
               "close": (30,)}
SLOT_N = 20
QS = (0.90, 0.95, 0.98)
XS_LEGS, XS_MIN_NAMES = 2, 8
FEATS = list(FEATURES) + ["slot_ret"]
assert all(set(v) <= set(DECISIONS) for v in DECISIONS_H.values())


def _exit(a: dict, k: int, n: int | None) -> float:
    last = a["last"]
    if n is None or k + n > last:              # same rule as the brains label
        return float(a["close"][last])
    return float(a["open"][k + n])


def build_rows(bars: pd.DataFrame, daily: pd.DataFrame, spy_bars: pd.DataFrame,
               qqq_bars: pd.DataFrame) -> pd.DataFrame:
    """One name: brains features at every brains decision minute + ``y_<H>`` / ``slot_<H>`` for each
    horizon whose grid contains that minute (NaN otherwise). Sessions ascending."""
    grid, spy, qqq = to_grid(bars), to_grid(spy_bars), to_grid(qqq_bars)
    dv_hist: dict[int, list[float]] = {k: [] for k in DECISIONS}
    y_hist: dict[tuple[str, int], list[float]] = {(h, k): [] for h, ks in DECISIONS_H.items() for k in ks}
    rows: list[dict] = []
    for sess in sorted(grid):
        a = grid[sess]
        slot_dv = {k: float(np.mean(dv_hist[k][-SLOT_LOOKBACK:])) if len(dv_hist[k]) >= SLOT_LOOKBACK else None
                   for k in DECISIONS}
        new = session_rows(sess, a, daily, slot_dv, spy.get(sess), qqq.get(sess))
        for r in new:
            k = r["k"]
            dv_hist[k].append(r.pop("_dv"))
            r.pop("label")                     # replaced by y_<H>
            for h, n in HORIZONS.items():
                if k not in DECISIONS_H[h]:
                    r[f"y_{h}"] = r[f"slot_{h}"] = np.nan
                    continue
                hist = y_hist[(h, k)]
                r[f"slot_{h}"] = float(np.mean(hist[-SLOT_N:])) if len(hist) >= SLOT_N else np.nan
                r[f"y_{h}"] = _exit(a, k, n) / float(a["open"][k]) - 1.0
        for r in new:                          # today's labels join the history only after today's rows
            for h in HORIZONS:
                if r["k"] in DECISIONS_H[h]:
                    y_hist[(h, r["k"])].append(r[f"y_{h}"])
        rows.extend(new)
    return pd.DataFrame(rows)


def horizon_frame(rows: pd.DataFrame, h: str) -> pd.DataFrame:
    """Rows on horizon ``h``'s decision grid with ``label`` and ``slot_ret`` set."""
    d = rows[rows.k.isin(DECISIONS_H[h])].copy()
    d["label"], d["slot_ret"] = d[f"y_{h}"], d[f"slot_{h}"]
    return d.drop(columns=[f"{p}_{x}" for x in HORIZONS for p in ("y", "slot")]).reset_index(drop=True)


def _fit(train: pd.DataFrame, target: np.ndarray, feats: list[str]):
    import lightgbm as lgb

    lo, hi = np.quantile(target, [0.01, 0.99])
    m = lgb.LGBMRegressor(**LGB_PARAMS)
    m.fit(train[feats], np.clip(target, lo, hi))
    return m


def _months(d: pd.DataFrame, start: str):
    m = pd.to_datetime(d.session).dt.to_period("M")
    for p in sorted(m.unique()):
        if p >= pd.Period(start, "M"):
            yield p, d[m < p], d[m == p]


def walk_forward_name(d: pd.DataFrame, start: str) -> pd.DataFrame:
    """Per-name monthly expanding walk-forward. Test rows get ``pred`` and in-sample prediction
    quantiles ``lo_<q>`` / ``hi_<q>`` for every q in QS."""
    out = []
    for _, train, test in _months(d, start):
        if train.session.nunique() < MIN_TRAIN_SESSIONS or test.empty:
            continue
        m = _fit(train, train.label.to_numpy(float), FEATS)
        ins = m.predict(train[FEATS])
        t = test.assign(pred=m.predict(test[FEATS]))
        for q in QS:
            t[f"lo_{q}"], t[f"hi_{q}"] = np.quantile(ins, [1 - q, q])
        out.append(t)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def walk_forward_pooled(frames: dict[str, pd.DataFrame], start: str) -> pd.DataFrame:
    """One pooled model (features + name id) on the label minus its cross-sectional decision mean."""
    names = sorted(frames)
    d = pd.concat([f.assign(sym=s, tid=names.index(s)) for s, f in frames.items()], ignore_index=True)
    d["xs_target"] = d.label - d.groupby(["session", "k"]).label.transform("mean")
    feats = FEATS + ["tid"]
    out = []
    for _, train, test in _months(d, start):
        if train.session.nunique() < MIN_TRAIN_SESSIONS or test.empty:
            continue
        m = _fit(train, train.xs_target.to_numpy(float), feats)
        out.append(test.assign(pred=m.predict(test[feats])))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def name_positions(t: pd.DataFrame, q: float) -> pd.Series:
    return pd.Series(np.where(t.pred >= t[f"hi_{q}"], 1, np.where(t.pred <= t[f"lo_{q}"], -1, 0)), index=t.index)


def name_book(trades: dict[str, pd.DataFrame], q: float, cost: float, n_names: int, days) -> pd.Series:
    """Daily net over ``days``: each name has a fixed 1/n_names of capital; no trade earns 0."""
    per = []
    for s, t in trades.items():
        if t.empty:
            continue
        pos = name_positions(t, q)
        net = pos * t.label - (pos != 0) * cost
        per.append(net.groupby(t.session).sum().rename(s))
    if not per:
        return pd.Series(0.0, index=days)
    return (pd.concat(per, axis=1).fillna(0.0).sum(axis=1) / n_names).reindex(days, fill_value=0.0)


def xs_book(pooled: pd.DataFrame, cost: float, days) -> pd.Series:
    """Long the XS_LEGS highest, short the XS_LEGS lowest predictions per decision; capital split
    over the legs; decisions with fewer than XS_MIN_NAMES predictions do not trade."""
    p = pooled.dropna(subset=["pred", "label"])
    rows = []
    for (sess, _k), g in p.groupby(["session", "k"], sort=True):
        if len(g) < XS_MIN_NAMES:
            continue
        g = g.sort_values(["pred", "sym"])
        legs = np.r_[-g.label.to_numpy()[:XS_LEGS], g.label.to_numpy()[-XS_LEGS:]]
        rows.append((sess, float(legs.mean()) - cost))
    if not rows:
        return pd.Series(0.0, index=days)
    return pd.DataFrame(rows, columns=["session", "net"]).groupby("session").net.sum().reindex(days, fill_value=0.0)


def sharpe(p: pd.Series) -> float:
    s = p.std(ddof=1)
    return float(p.mean() / s * np.sqrt(252)) if s and s > 0 else float("nan")


def lower_bound(x: np.ndarray, alpha: float, rng: np.random.Generator, n_boot: int = 10_000) -> float:
    idx = rng.integers(0, len(x), size=(n_boot, len(x)))
    return float(np.quantile(x[idx].mean(axis=1), alpha))
