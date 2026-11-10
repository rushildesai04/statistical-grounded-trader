"""Beta ground rule (owner 2026-11-10): "the best tickers to look for are ones with a beta value of 3 or higher ...
prioritize those to throw into the funnel from the alphas [Alpaca movers] and any others we source outside of the
MetaTraders alerts. This should be a ground rule."

Beta = raw OLS beta of daily returns vs SPY over the last WINDOW sessions (split-adjusted SIP daily bars; at least
MIN_OBS returns, else unknown), computed with ``sgt.risk.sizing.asset_risk``. Cached once per symbol per session in
``symbol_beta``. Unknown beta never blocks a symbol: it is ranked after the high-beta names, in its original order.
Priority only (which symbols the scanner watches first, what the text shows); not a tested filter.
"""
from __future__ import annotations

import logging
import math
from typing import Callable

import pandas as pd

from sgt.ops.store import OpsStore, iso, utcnow
from sgt.risk.sizing import asset_risk

log = logging.getLogger(__name__)
HIGH_BETA, WINDOW, MIN_OBS, MARKET = 3.0, 252, 60, "SPY"
Daily = Callable[[list[str]], dict[str, pd.DataFrame]]   # symbols -> daily OHLC frames indexed by date


def compute(daily: dict[str, pd.DataFrame], symbols: list[str]) -> dict[str, tuple[float | None, int]]:
    """(raw beta or None, observations) per symbol from daily frames that include MARKET."""
    mkt = daily.get(MARKET)
    out: dict[str, tuple[float | None, int]] = {}
    for s in symbols:
        f = daily.get(s)
        if mkt is None or f is None or f.empty:
            out[s] = (None, 0)
            continue
        r = asset_risk(s, f, mkt["close"], window=WINDOW)
        out[s] = (r.raw_beta if r.n_obs >= MIN_OBS and not math.isnan(r.raw_beta) else None, r.n_obs)
    return out


def betas(db: OpsStore, symbols: list[str], session: str, fetch: Daily) -> dict[str, float | None]:
    """Session-cached betas; fetches only symbols not yet computed today. A fetch failure leaves them unknown
    (logged) and is retried on the next call."""
    syms = list(dict.fromkeys(symbols))
    with db.tx() as c:
        have = {r["symbol"]: r["beta"] for r in c.execute(
            "SELECT symbol, beta FROM symbol_beta WHERE session=? AND symbol IN (%s)" % ",".join("?" * len(syms)),
            (session, *syms))} if syms else {}
    missing = [s for s in syms if s not in have]
    if missing:
        try:
            daily = fetch(missing + [MARKET])
        except Exception as exc:  # noqa: BLE001 - priority only: never block the scanner; logged, retried next call
            log.warning("beta fetch failed for %d symbols: %s", len(missing), type(exc).__name__)
            return {s: have.get(s) for s in syms}
        now = iso(utcnow())
        with db.tx() as c:
            for s, (b, n) in compute(daily, missing).items():
                c.execute("INSERT OR REPLACE INTO symbol_beta(symbol, session, beta, n_obs, computed_at) VALUES "
                          "(?,?,?,?,?)", (s, session, b, n, now))
                have[s] = b
    return {s: have.get(s) for s in syms}


def prioritize(symbols: list[str], beta: dict[str, float | None]) -> list[str]:
    """Beta >= HIGH_BETA first (highest beta first), then everything else in its original order."""
    high = sorted((s for s in symbols if (beta.get(s) or 0) >= HIGH_BETA), key=lambda s: -beta[s])
    return high + [s for s in symbols if s not in set(high)]


def label(b: float | None) -> str:
    """'β 3.8 (high)' / 'β 1.2' / 'β n/a' for texts."""
    if b is None:
        return "β n/a"
    return f"β {b:.1f}" + (" (high)" if b >= HIGH_BETA else "")
