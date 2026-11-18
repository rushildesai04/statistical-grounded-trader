"""Where in the move does the alert arrive, and a long entry that waits for the low to hold.
(docs/findings/prereg_scenario_map_2026-11-18.md)

    .venv/bin/python scripts/scenario_map_study.py             # training, run ONCE

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_setups_study import D_STAR, latest  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/scenario_map")
AT_HIGH = -0.02                       # OFF at or above this = at the high
MIN_BEFORE, MIN_CELL = 5, 40
HORIZONS = (5, 15, 30, 60)
HOLDS = (1, 2)
ALPHA, SEED, N_BOOT = 0.05, 20261118, 10_000
BANDS = ("early", "middle", "late")
ET = "America/New_York"


def _idx(n_sessions: int) -> np.ndarray:
    return np.random.default_rng(SEED).integers(0, n_sessions, size=(N_BOOT, n_sessions))


def boot_mean(sess: np.ndarray, val: np.ndarray) -> np.ndarray:
    """Session-clustered bootstrap of a per-row mean (seed and resamples as the frozen plan)."""
    u, inv = np.unique(sess, return_inverse=True)
    s, n = np.bincount(inv, val, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = _idx(len(u))
    den = n[b].sum(1)
    return s[b].sum(1)[den > 0] / den[den > 0]


def boot_groups(sa: np.ndarray, va: np.ndarray, sb: np.ndarray, vb: np.ndarray) -> np.ndarray:
    """Session-clustered bootstrap of mean(a) - mean(b), sessions resampled jointly."""
    u = np.unique(np.concatenate([sa, sb]))
    ia, ib = np.searchsorted(u, sa), np.searchsorted(u, sb)
    s_a, n_a = np.bincount(ia, va, len(u)), np.bincount(ia, minlength=len(u)).astype(float)
    s_b, n_b = np.bincount(ib, vb, len(u)), np.bincount(ib, minlength=len(u)).astype(float)
    b = _idx(len(u))
    da, db = n_a[b].sum(1), n_b[b].sum(1)
    ok = (da > 0) & (db > 0)
    return s_a[b].sum(1)[ok] / da[ok] - s_b[b].sum(1)[ok] / db[ok]


def events(split: str = "train") -> pd.DataFrame:
    """The tested fade events of one split with the short's stored net, the news flag and the research start bar."""
    conv = {"symbol": str}
    name = "dev" if split == "train" else "holdout"
    e = pd.read_csv(f"reports/fade_news/{name}_events.csv", converters=conv)[
        ["symbol", "session", "alert_type", "net", "news_any"]].rename(columns={"net": "short_net"})
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv", converters=conv)[["symbol", "session", "entry_ts", "cost_rt"]]
    e = e.merge(src, on=["symbol", "session"]).dropna(subset=["entry_ts"])
    e = e.assign(session=e.session.astype(str), t0=pd.to_datetime(e.entry_ts, utc=True))
    e = e[e.session < D_STAR] if split == "train" else e[e.session >= D_STAR]
    return e.sort_values(["session", "symbol"], kind="stable").reset_index(drop=True)


def state(day: pd.DataFrame, t0: pd.Timestamp) -> dict | None:
    """The move's state at t0 from bars BEFORE it (all segments of the day): RUN, OFF and what goes with them."""
    before = day[day.ts < t0]
    at = day[day.ts == t0]
    if len(before) < MIN_BEFORE or at.empty:
        return None
    px = float(at.open.iloc[0])
    lo, hi = float(before.low.min()), float(before.high.max())
    if not (px > 0 and lo > 0 and hi > 0):
        return None
    hi_ts = before.ts[before.high.idxmax()]
    last5 = before.tail(5)
    return {"px": px, "run": px / lo - 1, "off": px / hi - 1, "mins_since_high": (t0 - hi_ts).total_seconds() / 60,
            "swing": float(((last5.high - last5.low) / last5.close).median())}


def forward(rth: pd.DataFrame, t0: pd.Timestamp, px: float, cost: float) -> dict:
    """Buy open[t0], sell the close h minutes later (the last bar at or before t0 + h - 1 minutes); the path's extremes."""
    out = {}
    for h in HORIZONS:
        w = rth[(rth.ts >= t0) & (rth.ts <= t0 + (h - 1) * pt.BAR)]
        out[f"long_{h}"] = float(w.close.iloc[-1]) / px - 1 - cost if len(w) else np.nan
    w = rth[(rth.ts >= t0) & (rth.ts < t0 + 30 * pt.BAR)]
    out["up_30"] = float(w.high.max()) / px - 1 if len(w) else np.nan
    out["down_30"] = float(w.low.min()) / px - 1 if len(w) else np.nan
    return out


def entries(rth: pd.DataFrame, t0: pd.Timestamp, cut: pd.Timestamp, cost: float) -> dict:
    """NOW (buy the open after the first red candle) and HOLDk (only after k more candles kept their low above the
    red candle's): each trade's net at equal size, or NaN when the leg does not trade."""
    out = {"now": np.nan, "now_fill_bar_stop": np.nan, **{f"hold{k}": np.nan for k in HOLDS},
           **{f"hold{k}_fill_bar_stop": np.nan for k in HOLDS}, "has_red": False}
    found = [s for s in pt.first_red(rth, t0) if s.trigger is None]
    if not found:
        return out
    s0 = found[0]
    out["has_red"] = True

    def run(ready: pd.Timestamp, key: str) -> None:
        t = pt.simulate(rth, pt.Setup("L4a", "long", ready, None, s0.stop), latest=cut)
        if t is not None:
            out[key] = t.gross - cost
            out[f"{key}_fill_bar_stop"] = float(t.reason == "stop" and t.exit_ts == t.entry_ts)
    run(s0.ready, "now")
    after = rth[rth.ts >= s0.ready].reset_index(drop=True)
    for k in HOLDS:
        if len(after) <= k or not (after.low.iloc[:k] > s0.stop).all():
            continue                                    # too few candles, or the low gave way: no trade
        run(after.ts.iloc[k], f"hold{k}")
    return out


def collect(ev: pd.DataFrame, store: ParquetBarStore) -> tuple[pd.DataFrame, int]:
    rows, skipped = [], 0
    for e in ev.itertuples(index=False):
        d = date.fromisoformat(e.session)
        f = store.read_frame(e.symbol, d, d)
        if not len(f):
            skipped += 1
            continue
        f = f.assign(ts=pd.to_datetime(f.ts, utc=True)).sort_values("ts").reset_index(drop=True)
        t0 = pd.Timestamp(e.t0)
        st = state(f, t0)
        rth = f[f.segment == "rth"].reset_index(drop=True)
        if st is None or not (rth.ts == t0).any():
            skipped += 1
            continue
        cost = float(e.cost_rt)
        et = t0.tz_convert(ET)
        rows.append({"symbol": e.symbol, "session": e.session, "alert_type": e.alert_type, "news": bool(e.news_any),
                     "short": float(e.short_net), "cost": cost, "minute": et.hour * 60 + et.minute, **st,
                     **forward(rth, t0, st["px"], cost), **entries(rth, t0, latest(e.session), cost)})
    return pd.DataFrame(rows), skipped


def assign(t: pd.DataFrame, edges: tuple[float, float] | None = None) -> tuple[pd.DataFrame, tuple[float, float]]:
    """Scenario of each event: RUN band (terciles; ``edges`` fixed from training when given) x at / off the high."""
    if edges is None:
        edges = (float(t.run.quantile(1 / 3)), float(t.run.quantile(2 / 3)))
    band = np.where(t.run <= edges[0], BANDS[0], np.where(t.run <= edges[1], BANDS[1], BANDS[2]))
    where = np.where(t.off >= AT_HIGH, "at the high", "off the high")
    return t.assign(band=band, where=where, scenario=[f"{b}, {w}" for b, w in zip(band, where)]), edges


def lower(sess: np.ndarray, val: np.ndarray, alpha: float) -> float | None:
    keep = np.isfinite(val)
    if keep.sum() < 2 or len(np.unique(sess[keep])) < 2:
        return None
    return float(np.quantile(boot_mean(sess[keep], val[keep]), alpha))


def describe(g: pd.DataFrame) -> dict:
    """The reported numbers of one group of events."""
    def m(col):
        x = g[col].dropna()
        return {"n": int(len(x)), "mean": float(x.mean()) if len(x) else None, "win": float((x > 0).mean()) if len(x) else None}
    out = {"events": int(len(g)), "run_median": float(g.run.median()) if len(g) else None,
           "off_median": float(g.off.median()) if len(g) else None,
           "up_30_median": float(g.up_30.median()) if len(g) else None, "down_30_median": float(g.down_30.median()) if len(g) else None,
           "short": m("short"), **{f"long_{h}": m(f"long_{h}") for h in HORIZONS}, "now": m("now"),
           **{f"hold{k}": m(f"hold{k}") for k in HOLDS}}
    for key in ("now", *(f"hold{k}" for k in HOLDS)):
        x = g[f"{key}_fill_bar_stop"].dropna()
        out[key]["stopped_on_entry_bar"] = float(x.mean()) if len(x) else None
    return out


def evaluate(t: pd.DataFrame) -> dict:
    sess = t.session.to_numpy()
    a_alpha = ALPHA / (2 * len(BANDS) * 2)
    res: dict = {"events": int(len(t)), "sessions": int(t.session.nunique()), "all": describe(t), "scenarios": {},
                 "long_scenarios": [], "short_scenarios": []}
    for name, g in t.groupby("scenario"):
        d = describe(g)
        inside = (t.scenario == name).to_numpy()
        lo_long = lower(sess[inside], t.long_30.to_numpy(float)[inside], a_alpha) if len(g) >= MIN_CELL else None
        lo_short = None
        if len(g) >= MIN_CELL and (~inside).sum() >= MIN_CELL:
            diff = boot_groups(sess[inside], t.short.to_numpy(float)[inside], sess[~inside], t.short.to_numpy(float)[~inside])
            lo_short = float(np.quantile(diff, a_alpha))
        d.update(long_30_lower=lo_long, short_minus_rest=float(g.short.mean() - t.short[~inside].mean()), short_diff_lower=lo_short,
                 is_long=bool(lo_long is not None and lo_long > 0), is_short=bool(lo_short is not None and lo_short > 0))
        res["scenarios"][name] = d
        res["long_scenarios"] += [name] if d["is_long"] else []
        res["short_scenarios"] += [name] if d["is_short"] else []
    b_alpha = ALPHA / len(HOLDS)
    res["hold"] = {}
    for k in HOLDS:
        col = f"hold{k}"
        x = t[col].to_numpy(float)
        lo = lower(sess, x, b_alpha)
        paired = (t[col].fillna(0.0) - t.now.fillna(0.0)).to_numpy(float)
        pm = boot_mean(sess, paired)
        res["hold"][col] = {"trades": int(np.isfinite(x).sum()), "share_of_red_events": float(np.isfinite(x).sum() / max(1, int(t.has_red.sum()))),
                            "mean": float(np.nanmean(x)) if np.isfinite(x).any() else None, "win": float((x[np.isfinite(x)] > 0).mean()) if np.isfinite(x).any() else None,
                            "lower": lo, "pass": bool(lo is not None and lo > 0),
                            "vs_now_per_event": {"mean": float(paired.mean()), "lo95": float(np.quantile(pm, 0.025)), "hi95": float(np.quantile(pm, 0.975))}}
    res["hold_pass"] = [k for k, v in res["hold"].items() if v["pass"]]
    splits = {"news": t.news.map({True: "news", False: "no news"}), "alert type": t.alert_type,
              "time": np.where(t.minute < 630, "first hour", "rest of day"),
              "price": pd.cut(t.px, [0, 2, 5, 10, np.inf], labels=["$1-2", "$2-5", "$5-10", "above $10"]).astype(str),
              "swing before": pd.qcut(t.swing, 3, labels=["calm", "middle", "wild"], duplicates="drop").astype(str),
              "minutes since the high": pd.cut(t.mins_since_high, [-1, 2, 10, 60, np.inf], labels=["0-2", "2-10", "10-60", "over 60"]).astype(str)}
    res["splits"] = {name: {str(k): describe(g) for k, g in t.groupby(np.asarray(key))} for name, key in splits.items()}
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    res_path = a.out / "result_train.json"
    if res_path.is_file():
        print(f"{res_path} exists: the training run was made once and is not repeated")
        return 2
    t, skipped = collect(events("train"), ParquetBarStore("data/alpaca"))
    if t.empty:
        raise SystemExit("no events: nothing reported")
    t, edges = assign(t)
    res = {"prereg": "docs/findings/prereg_scenario_map_2026-11-18.md", "left_out": int(skipped), "run_edges": list(edges),
           **evaluate(t)}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    t.to_csv(a.out / "train_events.csv", index=False)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
