"""Model Tape: the daily per-ticker model's own record, the way the Fade Tape shows the fade (owner 2026-11-06: "see
what the results of our current model provide" on NVDA, GOOGL, AAOI ...).

Input per ticker: the walk-forward out-of-sample frame (``session``, ``label`` = next close-to-close return, ``pred``,
``position`` in {-1, 0, 1}; each month predicted by a model trained only on earlier months) and daily OHLC bars.
Pure functions; the page renders with ``render``.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

import pandas as pd

COST_RT = 0.0004                 # sgt.megacap.live.COST_RT (market-on-close in and out)
TEMPLATE = Path(__file__).with_name("templates") / "model_tape.html"
RECENT = 126                     # sessions shown as candles (about six months)


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def stats(oos: pd.DataFrame) -> dict:
    """Strategy vs buy-and-hold on the out-of-sample days. Positions are taken at the close of ``session`` and earn
    ``label`` (the next close-to-close return), minus the round trip when a position is held."""
    t = oos.dropna(subset=["label", "position"]).copy()
    t["net"] = t.position * t.label - COST_RT * (t.position != 0)
    traded = t[t.position != 0]
    yr = pd.to_datetime(t.session).dt.year

    def sharpe(x: pd.Series) -> float | None:
        return _f(x.mean() / x.std() * math.sqrt(252)) if len(x) > 2 and x.std() > 0 else None

    by_year = {int(y): {"model": _f(g.net.sum()), "hold": _f(g.label.sum())} for y, g in t.groupby(yr)}
    last = t.tail(63)
    return {"days": len(t), "traded_share": _f(len(traded) / len(t)) if len(t) else None,
            "hit": _f((traded.net > 0).mean()) if len(traded) else None,
            "per_trade_bp": _f(traded.net.mean() * 1e4) if len(traded) else None,
            "model_ann": _f(t.net.mean() * 252), "hold_ann": _f(t.label.mean() * 252),
            "model_sharpe": sharpe(t.net), "hold_sharpe": sharpe(t.label),
            "long_share": _f((t.position > 0).mean()), "short_share": _f((t.position < 0).mean()),
            "last63_model": _f(last.net.sum()), "last63_hold": _f(last.label.sum()),
            "ic": _f(t.pred.corr(t.label, method="spearman")) if "pred" in t else None, "by_year": by_year}


def view(symbol: str, group: str, oos: pd.DataFrame, daily: pd.DataFrame) -> dict:
    """One ticker card: stats, the last ~6 months of daily candles with the model's calls, and equity curves."""
    t = oos.dropna(subset=["label", "position"]).copy()
    t["session"] = t.session.astype(str)
    t["net"] = t.position * t.label - COST_RT * (t.position != 0)
    d = daily.assign(date=pd.to_datetime(daily.date).dt.strftime("%Y-%m-%d")).drop_duplicates("date")
    recent = t.tail(RECENT)
    days = set(recent.session)
    candles = [{"time": r.date, "open": _f(r.open), "high": _f(r.high), "low": _f(r.low), "close": _f(r.close)}
               for r in d[d.date.isin(days | {d.date.max()})].itertuples(index=False)]
    marks = [{"time": r.session, "pos": int(r.position), "win": bool(r.net > 0), "ret": _f(r.label)}
             for r in recent.itertuples(index=False) if r.position != 0]
    eq = t.assign(m=(1 + t.net).cumprod(), h=(1 + t.label).cumprod())
    wk = eq.groupby(pd.to_datetime(eq.session).dt.to_period("W")).tail(1)          # weekly points keep the page small
    equity = [{"time": r.session, "model": _f(r.m), "hold": _f(r.h)} for r in wk.itertuples(index=False)]
    last = t.iloc[-1] if len(t) else None
    return {"symbol": symbol, "group": group, "stats": stats(oos), "candles": candles, "marks": marks,
            "equity": equity, "last": {"session": last.session, "position": int(last.position),
                                       "pred": _f(last.pred)} if last is not None else None}


def render(views: list[dict], generated: datetime, forward: list[dict] | None = None) -> str:
    data = {"generated": generated.strftime("%a %b %-d, %Y %-I:%M %p"), "tickers": views, "forward": forward or []}
    payload = json.dumps(data, allow_nan=False).replace("<", "\\u003c")
    return TEMPLATE.read_text().replace("/*MODELS*/null", payload, 1)
