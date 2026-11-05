"""Learned filter for the alert fade, v1 (prereg_fade_meta_2026-11-04): point-in-time features + model.

One pooled model across tickers (traits, not names). Features read only regular-session 1-minute bars that
ENDED at or before the alert (bar start + 1 min <= posted_at). Scoring the shadow book uses official SIP bars
after the fact, which is still point in time and avoids an IEX-vs-SIP train/serve gap.
"""
from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ET = "America/New_York"
TYPES = ("volume_increasing", "volume_spike", "gaining_volume", "gaining_momentum", "new_high", "intraday_high",
         "testing_high", "price_level", "resumed", "news")
FEATURES = ["f_logpx", "f_r_open", "f_r5", "f_r15", "f_r60", "f_off_high", "f_vol5_rel", "f_minute", "f_dow",
            "f_type", "f_prev_events", "f_spy_open"]
PARAMS = dict(n_estimators=200, learning_rate=0.03, num_leaves=7, min_child_samples=40, colsample_bytree=0.8,
              subsample=0.8, subsample_freq=1, random_state=0, verbose=-1)
QS = (0.20, 0.35, 0.50)
MODEL_PATH = Path("config/models/fade_meta_v1.txt")


def rth_before(bars: pd.DataFrame, posted_at: pd.Timestamp) -> pd.DataFrame:
    """Regular-session bars of the alert's session that ended at or before ``posted_at``."""
    if bars.empty:
        return bars
    b = bars.assign(ts=pd.to_datetime(bars.ts, utc=True))
    b = b[(b.segment == "rth") & (b.ts + pd.Timedelta(minutes=1) <= posted_at)]
    return b.sort_values("ts")


def _ret(c: np.ndarray, n: int) -> float:
    return float(c[-1] / c[-1 - n] - 1) if len(c) > n else (float(c[-1] / c[0] - 1) if len(c) > 1 else 0.0)


def features(posted_at: pd.Timestamp, alert_type: str, bars: pd.DataFrame, spy: pd.DataFrame,
             prev_events: int) -> dict:
    """One event's feature row (NaN where the session has no bar yet, e.g. an alert in the first minute)."""
    b = rth_before(bars, posted_at)
    et = posted_at.tz_convert(ET)
    row = {k: np.nan for k in FEATURES}
    row.update(f_minute=float((et.hour - 9) * 60 + et.minute - 30), f_dow=float(et.dayofweek),
               f_type=float(TYPES.index(alert_type)) if alert_type in TYPES else np.nan,
               f_prev_events=float(prev_events))
    if len(b):
        c, v = b.close.to_numpy(float), b.volume.to_numpy(float)
        row.update(f_logpx=float(np.log(c[-1])), f_r_open=float(c[-1] / b.open.iloc[0] - 1),
                   f_r5=_ret(c, 5), f_r15=_ret(c, 15), f_r60=_ret(c, 60),
                   f_off_high=float(c[-1] / b.high.max() - 1),
                   f_vol5_rel=float(v[-5:].mean() / v.mean()) if v.mean() > 0 else np.nan)
    s = rth_before(spy, posted_at)
    if len(s):
        row["f_spy_open"] = float(s.close.iloc[-1] / s.open.iloc[0] - 1)
    return row


def prev_event_counts(ev: pd.DataFrame, lookback: int = 10) -> pd.Series:
    """Per event: earlier sessions (within the last ``lookback`` sessions with any event) where the same
    symbol had an event. Uses only the event list itself (known before the alert)."""
    days = sorted(ev.session.unique())
    pos = {d: i for i, d in enumerate(days)}
    seen: dict[str, list[int]] = {}
    out = []
    for r in ev.sort_values("posted_at").itertuples():
        i = pos[r.session]
        hist = [j for j in seen.get(r.symbol, []) if i - lookback <= j < i]
        out.append((r.Index, len(hist)))
        seen.setdefault(r.symbol, []).append(i)
    return pd.Series(dict(out)).reindex(ev.index)


def build(ev: pd.DataFrame, store, spy_symbol: str = "SPY") -> pd.DataFrame:
    """Feature rows for events (``symbol``, ``alert_type``, ``posted_at``, ``session``) from a bar store."""
    ev = ev.assign(posted_at=pd.to_datetime(ev.posted_at, utc=True))
    prev = prev_event_counts(ev)
    rows, spy_cache = [], {}
    for r in ev.itertuples():
        d = date.fromisoformat(str(r.session))
        if d not in spy_cache:
            spy_cache[d] = store.read_frame(spy_symbol, d, d)
        rows.append(features(r.posted_at, r.alert_type, store.read_frame(r.symbol, d, d), spy_cache[d],
                             int(prev[r.Index])))
    return pd.DataFrame(rows, index=ev.index)


def fit(x: pd.DataFrame, y: np.ndarray):
    import lightgbm as lgb

    lo, hi = np.quantile(y, [0.01, 0.99])
    return lgb.LGBMRegressor(**PARAMS).fit(x[FEATURES], np.clip(y, lo, hi))


def take(pred: np.ndarray, insample: np.ndarray, q: float) -> np.ndarray:
    """True = take the trade: prediction at or above the q-quantile of in-sample predictions (q=0: take all)."""
    return np.ones(len(pred), bool) if q == 0 else pred >= np.quantile(insample, q)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Fade brain v2 (prereg_fade_brain_2026-11-05): v1 features + tape signals + news + SEC filings, used for SIZING.
SIG_KEYS = ("ema9_21_gap_pct", "ema_cross_state", "minutes_since_cross", "vwap_dist_pct", "rsi14", "macd_hist_pct",
            "rvol_5m", "atr14_1m_pct", "off_hod_pct", "above_premarket_high", "pvt_trend", "psar_side",
            "minutes_since_open")
BRAIN_FEATURES = FEATURES + [f"s_{k}" for k in SIG_KEYS] + ["n_own", "n_earn", "f_8k", "f_dil"]
WEIGHTS = (0.5, 1.0, 1.5)                      # bottom / middle / top third of in-sample predictions
BRAIN_PATH = Path("config/models/fade_brain_v2.txt")


def signal_features(ev: pd.DataFrame, store) -> pd.DataFrame:
    """``s_*`` tape signals at each alert from the session's bars that ended before it (sgt.fast.signals)."""
    from sgt.fast import signals

    rows = []
    for r in ev.itertuples():
        d = date.fromisoformat(str(r.session))
        f = store.read_frame(r.symbol, d, d)
        snap = signals.snapshot(f, pd.Timestamp(r.posted_at)) if len(f) else {}
        rows.append({f"s_{k}": (np.nan if snap.get(k) is None else float(snap[k])) for k in SIG_KEYS})
    return pd.DataFrame(rows, index=ev.index)


def news_features(ev: pd.DataFrame) -> pd.DataFrame:
    """News and SEC flags already computed point in time (prereg_fade_news / prereg_fade_filings event files)."""
    flag = lambda s: s.astype(str).str.lower().isin(["true", "1", "1.0"]).astype(float)  # noqa: E731
    out = pd.DataFrame({"n_own": flag(ev.news_own), "n_earn": flag(ev.news_earn)}, index=ev.index)
    known = ev.get("known", pd.Series(1, index=ev.index)).fillna(0).astype(int) == 1
    for c in ("f_8k", "f_dil"):
        out[c] = ev[c].astype(float).where(known) if c in ev else np.nan
    return out


def fit_cols(x: pd.DataFrame, y: np.ndarray, cols: list[str]):
    import lightgbm as lgb

    lo, hi = np.quantile(y, [0.01, 0.99])
    return lgb.LGBMRegressor(**PARAMS).fit(x[cols], np.clip(y, lo, hi))


def size_weight(pred: np.ndarray, insample: np.ndarray) -> np.ndarray:
    """0.5x / 1.0x / 1.5x by the prediction's tercile among the model's in-sample predictions."""
    lo, hi = np.quantile(insample, [1 / 3, 2 / 3])
    return np.where(pred < lo, WEIGHTS[0], np.where(pred < hi, WEIGHTS[1], WEIGHTS[2]))
