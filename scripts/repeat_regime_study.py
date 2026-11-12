"""R1 longs on repeat alerts, R2 shorts after hot-tape days (docs/findings/prereg_repeat_regime_2026-11-12.md).

    .venv/bin/python scripts/repeat_regime_study.py      # run ONCE (frozen prereg); no bars re-read
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fade_repeat_days_study as rd  # noqa: E402
from long_risk_study import weights  # noqa: E402
from trader_rules_long_study import _rng_idx, boot_groups  # noqa: E402

from sgt.intraday.calendar import default_calendar  # noqa: E402

OUT = Path("reports/repeat_regime")
ALPHA, MIN_TRAIN, MIN_HOLD, D_STAR, Q_HOT = 0.05 / 2, 100, 30, "2026-07-01", 0.75


def _p(x) -> float:
    x = np.asarray(x, float)
    return float(((x <= 0) | np.isnan(x)).mean())


def flag_repeats(rows: pd.DataFrame, events: pd.DataFrame, window: int = rd.WINDOW) -> pd.Series:
    """Repeat flag for arbitrary (symbol, session) rows against fade events 1..window calendar days earlier."""
    ev = events.assign(day=pd.to_datetime(events.session))[["symbol", "day"]]
    days = pd.to_datetime(rows.session)
    by = {s: g.day.to_numpy() for s, g in ev.groupby("symbol")}
    out = []
    for s, d in zip(rows.symbol, days):
        prior = by.get(s)
        gap = (d.to_datetime64() - prior).astype("timedelta64[D]").astype(int) if prior is not None else np.array([])
        out.append(bool(((gap >= 1) & (gap <= window)).any()))
    return pd.Series(out, index=rows.index)


def prior_counts(events: pd.DataFrame, sessions: pd.Series, prev=None) -> pd.Series:
    """Number of fade events on the previous NYSE session for each session (0 if none)."""
    cnt = events.groupby("session").size()
    prev = prev or (lambda s: default_calendar().previous_sessions(date.fromisoformat(s), 1)[0].isoformat())
    cache = {s: int(cnt.get(prev(s), 0)) for s in sessions.unique()}
    return sessions.map(cache)


def hot_threshold(events: pd.DataFrame) -> float:
    cnt = events[events.session < D_STAR].groupby("session").size()
    return float(cnt.quantile(Q_HOT))


def r1_stats(b: pd.DataFrame, d50: float) -> dict:
    """A2 per dollar on repeat longs minus on first-time longs (sessions resampled jointly)."""
    w = weights(b.d, d50).to_numpy(float)
    x = w * b.e_net.to_numpy(float)
    rep = b.repeat.to_numpy(bool)
    u, inv = np.unique(b.session.to_numpy(), return_inverse=True)
    sx_r, sw_r = np.bincount(inv, x * rep, len(u)), np.bincount(inv, w * rep, len(u))
    sx_n, sw_n = np.bincount(inv, x * ~rep, len(u)), np.bincount(inv, w * ~rep, len(u))
    idx = _rng_idx(len(u))
    dist = sx_r[idx].sum(1) / sw_r[idx].sum(1) - sx_n[idx].sum(1) / sw_n[idx].sum(1)
    pr = float(x[rep].sum() / w[rep].sum()) if rep.any() else None
    pn = float(x[~rep].sum() / w[~rep].sum()) if (~rep).any() else None
    return {"n_repeat": int(rep.sum()), "n_first": int((~rep).sum()), "repeat_per_dollar": pr, "first_per_dollar": pn,
            "diff": None if pr is None or pn is None else pr - pn, "p": _p(dist),
            "repeat_lower95": float(np.nanquantile(sx_r[idx].sum(1) / sw_r[idx].sum(1), 0.05))}


def r2_stats(f: pd.DataFrame) -> dict:
    """Not-hot minus hot mean net (sessions resampled jointly)."""
    f = f.dropna(subset=["net"])
    cool, hot = f[~f.hot], f[f.hot]
    out = {"n_cool": len(cool), "n_hot": len(hot), "cool": float(cool.net.mean()) if len(cool) else None,
           "hot": float(hot.net.mean()) if len(hot) else None, "hot_total": float(hot.net.sum())}
    if len(cool) and len(hot):
        dist = boot_groups(cool.session.to_numpy(), cool.net.to_numpy(float), hot.session.to_numpy(),
                           hot.net.to_numpy(float))
        out.update(diff=out["cool"] - out["hot"], p=_p(dist))
    return out


def r1_pass(st: dict, minimum: int = MIN_TRAIN) -> bool:
    return st["n_repeat"] >= minimum and (st.get("diff") or 0) > 0 and st["p"] < ALPHA


def r2_pass(st: dict, minimum: int = MIN_TRAIN) -> bool:
    return st["n_hot"] >= minimum and (st.get("diff") or 0) > 0 and st.get("p", 1.0) < ALPHA and st["hot_total"] <= 0


def main() -> int:
    events = rd.load()
    fades = rd.mark_repeats(events)
    q = hot_threshold(events)
    fades["hot"] = prior_counts(events, fades.session) > q
    pop = fades[(fades.news_own == 0) & ~fades.repeat]
    longs = pd.read_csv("reports/long_risk/base.csv", dtype={"session": str})
    longs["repeat"] = flag_repeats(longs, events)
    lt, lh = longs[longs.split == "train"], longs[longs.split == "holdout"]
    d50 = float(lt.d.quantile(0.5))
    r1, r2 = r1_stats(lt, d50), r2_stats(pop[pop.split == "dev"])
    out = {"prereg": "docs/findings/prereg_repeat_regime_2026-11-12.md", "alpha": ALPHA, "hot_threshold": q,
           "R1": {"train": r1, "pass": r1_pass(r1), "holdout": None, "adopt": False},
           "R2": {"train": r2, "pass": r2_pass(r2), "holdout": None, "adopt": False},
           "secondary": {"R2_all_nonews": r2_stats(fades[(fades.news_own == 0) & (fades.split == "dev")])}}
    if out["R1"]["pass"]:
        h = r1_stats(lh, d50)
        out["R1"].update(holdout=h, adopt=h["n_repeat"] >= MIN_HOLD and (h.get("diff") or 0) > 0)
    if out["R2"]["pass"]:
        h = r2_stats(pop[pop.split == "holdout"])
        out["R2"].update(holdout=h, adopt=h["n_hot"] >= MIN_HOLD and (h.get("diff") or 0) > 0 and h["hot_total"] <= 0)
    OUT.mkdir(parents=True, exist_ok=True)
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
