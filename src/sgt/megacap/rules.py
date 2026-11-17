"""Risk rules for the daily model's long calls (docs/findings/prereg_long_rules_2026-11-15.md and
prereg_long_market_filter_2026-11-16.md).

Rule E: no new long into the stock's own earnings. Rule V: a day whose calls move together is cut back to the typical
day's risk. Rule M: no new long call while the market is below its 200-day average. Every input is known at the
15:50 ET decision. Research only: nothing here sizes a book.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.megacap.score import CAP, GROSS

ET = "America/New_York"
CLOSE_HOUR = 16                 # early closes are treated as 16:00 (can only miss a flag, never add one)
LOOKBACK, MIN_OBS = 60, 40      # covariance window (sessions) and the fewest common sessions a pair needs
MIN_HIST = 60                   # earlier sessions with a long call before the cap exists
TREND = 200                     # rule M: sessions in the market's moving average


def hold_sessions(accepted: pd.Series, sessions: list[str]) -> set[str]:
    """Sessions D whose close-to-close hold contains a release: accepted at or after 16:00 ET on D and before 16:00 ET
    on the next session. ``sessions``: every trading session, sorted. A release after the last session's close has
    no known next session and maps to nothing."""
    if len(accepted) == 0 or len(sessions) < 2:
        return set()
    closes = pd.DatetimeIndex(pd.to_datetime(sessions)).tz_localize(ET) + pd.Timedelta(hours=CLOSE_HOUR)
    t = pd.DatetimeIndex(pd.to_datetime(accepted)).tz_convert(ET)
    i = closes.searchsorted(t, side="right") - 1          # the last close at or before the release
    return {sessions[k] for k in i if 0 <= k < len(sessions) - 1}


def earnings_flag(t: pd.DataFrame, earnings: dict[str, pd.Series]) -> pd.Series:
    """True on rows (symbol, session D) whose hold contains that symbol's own release. ``t`` must hold every session
    (the next session is read from it). A symbol without dates is never flagged."""
    sessions = sorted(t.session.unique())
    flag = pd.Series(False, index=t.index)
    for sym, accepted in earnings.items():
        hold = hold_sessions(accepted, sessions)
        if hold:
            flag |= (t.symbol == sym) & t.session.isin(hold)
    return flag


def scaled(t: pd.DataFrame, weight: pd.Series) -> pd.Series:
    """The weights ``score.book`` actually holds: per-call cap, then all of a day scaled down together above the
    gross cap. Returned so a rule can cut a call without the freed weight flowing to the day's other calls."""
    w = weight.where(t.position != 0, 0.0).clip(upper=CAP)
    gross = w.groupby(t.session).transform("sum")
    return w * (GROSS / gross).clip(upper=1.0).fillna(1.0)


def book_swing(t: pd.DataFrame, w: pd.Series) -> pd.Series:
    """Per session with a held call: the book's predicted daily swing sqrt(w'Cw). C = sample covariance of the called
    names' close-to-close returns (``label``) over the LOOKBACK rows ending two sessions before D: the row of D-1
    ends at D's own close, which the 15:50 decision has not seen. Too little history: variance d_rv21^2, covariance 0."""
    r = t.pivot(index="session", columns="symbol", values="label").sort_index()
    at = {s: i for i, s in enumerate(r.index)}
    held = t[w > 0]
    out: dict[str, float] = {}
    for d, g in held.groupby("session", sort=True):
        i = at[d]
        names = g.symbol.tolist()
        win = r.iloc[max(0, i - 1 - LOOKBACK):max(0, i - 1)][names]
        c = win.cov(min_periods=MIN_OBS).to_numpy(float).copy() if len(win) else np.full((len(names),) * 2, np.nan)
        own = g.d_rv21.to_numpy(float) ** 2
        for k in range(len(names)):
            if not np.isfinite(c[k, k]):
                c[k, k] = own[k]
        c[~np.isfinite(c)] = 0.0
        wv = w.loc[g.index].to_numpy(float)
        out[d] = float(np.sqrt(max(wv @ c @ wv, 0.0)))
    return pd.Series(out, dtype=float).sort_index()


def cap_factor(swing: pd.Series) -> pd.Series:
    """Per session: min(1, c / swing), c = the median swing of all EARLIER sessions (none until MIN_HIST of them:
    factor 1). Never above 1: the rule only cuts."""
    s = swing.sort_index()
    c = s.expanding(min_periods=MIN_HIST).median().shift(1)
    f = (c / s.where(s > 0)).clip(upper=1.0)
    return f.where(c.notna() & (s > 0), 1.0)


def below_trend(sessions: list[str], close: pd.Series, n: int = TREND) -> pd.Series:
    """Rule M flag per decision session D (index: ``sessions``, sorted): True when the market's close of the session
    BEFORE D is lower than the average of the ``n`` market closes ending at that session. ``close``: the index's
    daily closes by ISO session date. D's own close is never read (it is not known at 15:50). False with fewer than
    ``n`` closes up to the session before D, when that session has no close, and for the first session."""
    c = close.sort_index().astype(float)
    avg = c.rolling(n, min_periods=n).mean()
    low = (c < avg) & avg.notna()
    s = sorted(sessions)
    prev = [None, *s[:-1]]
    return pd.Series([bool(low.get(p, False)) if p is not None else False for p in prev], index=s, dtype=bool)
