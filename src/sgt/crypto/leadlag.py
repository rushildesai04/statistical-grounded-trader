"""C1 BTC lead-lag (prereg_crypto_leadlag_2026-11-04): events, basket returns, development gate. Pure functions.

Frames: ``ts`` (UTC minute start), ``open``, ``close`` (Coinbase candles; a missing minute had no trades).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

Q, WINDOW_MIN, GAP_MIN, HOLD_MIN, COST = 0.995, 30 * 1440, 10, 5, 0.0010
MIN_EVENTS, SEED, N_BOOT = 200, 20261104, 10_000


def btc_events(btc: pd.DataFrame, q: float = Q, window: int = WINDOW_MIN, gap: int = GAP_MIN) -> pd.DataFrame:
    """BTC minutes whose |log return| (vs the previous candle's close) is >= the q-quantile of |returns| over the
    ``window`` minutes before it (point in time; a full window is required). Events within ``gap`` minutes after
    a kept event are dropped. Returns ts, ret, thr."""
    b = btc.sort_values("ts").set_index("ts")
    r = np.log(b.close).diff().dropna()
    a = r.abs()
    thr = a.rolling(f"{window}min", min_periods=int(window * 0.9)).quantile(q).shift(1)
    span_ok = (a.index - a.index[0]) >= pd.Timedelta(minutes=window)
    hit = r[(a >= thr) & thr.notna() & span_ok]
    kept, last = [], None
    for t in hit.index:
        if last is None or t - last > pd.Timedelta(minutes=gap):
            kept.append(t)
            last = t
    return pd.DataFrame({"ts": kept, "ret": hit.loc[kept].to_numpy(), "thr": thr.loc[kept].to_numpy()})


def basket(events: pd.DataFrame, alts: dict[str, pd.DataFrame], hold: int = HOLD_MIN,
           cost: float = COST) -> pd.DataFrame:
    """Per event: equal-weight mean over alts with a candle at t+1 of sign(BTC) x (exit close / entry open - 1),
    entry = open of bar t+1, exit = close of the last candle in bars t+1 .. t+hold; minus ``cost`` per position."""
    idx = {s: f.sort_values("ts").set_index("ts") for s, f in alts.items() if not f.empty}
    rows = []
    for e in events.itertuples(index=False):
        t1, tn = e.ts + pd.Timedelta(minutes=1), e.ts + pd.Timedelta(minutes=hold)
        side = 1.0 if e.ret > 0 else -1.0
        per = {}
        for s, f in idx.items():
            if t1 not in f.index:
                continue
            w = f.loc[t1:tn]
            per[s] = side * (w.close.iloc[-1] / f.at[t1, "open"] - 1)
        if per:
            g = float(np.mean(list(per.values())))
            rows.append({"ts": e.ts, "btc_ret": e.ret, "n_alts": len(per), "gross": g, "net": g - cost, **{
                f"r_{s}": v for s, v in per.items()}})
    return pd.DataFrame(rows)


def gate(ev: pd.DataFrame, mid: pd.Timestamp, rng: np.random.Generator) -> dict:
    """Prereg development gate on event net returns (day-cluster bootstrap, halves split at ``mid``, month share)."""
    if len(ev) == 0:
        return {"n": 0, "pass": False}
    day = ev.ts.dt.floor("D")
    g = ev.groupby(day).net.agg(["sum", "count"])
    s, c = g["sum"].to_numpy(float), g["count"].to_numpy(float)
    i = rng.integers(0, len(g), size=(N_BOOT, len(g)))
    lower = float(np.quantile(s[i].sum(1) / c[i].sum(1), 0.05))
    halves = (float(ev.net[ev.ts < mid].mean()), float(ev.net[ev.ts >= mid].mean()))
    total = float(ev.net.sum())
    month = ev.groupby(ev.ts.dt.strftime("%Y-%m")).net.sum()
    top_share = float(month.max() / total) if total > 0 else float("nan")
    ok = (len(ev) >= MIN_EVENTS and ev.net.mean() > 0 and lower > 0 and min(halves) > 0
          and total > 0 and top_share <= 0.5)
    return {"n": len(ev), "mean": float(ev.net.mean()), "gross": float(ev.gross.mean()), "lower95": lower,
            "hit": float((ev.net > 0).mean()), "halves": halves, "top_month_share": top_share, "pass": bool(ok)}
