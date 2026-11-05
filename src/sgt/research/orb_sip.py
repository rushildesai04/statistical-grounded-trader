"""Opening-range breakout on the day's "stocks in play" — pure simulation core.

Faithful replication of Zarattini/Barbon/Aziz (2024) as frozen in
``docs/findings/prereg_orb_stocks_in_play_2026-11-05.md`` (that prereg is the spec).
Every function here is pure: it takes point-in-time frames/scalars and returns
numbers, with no network or disk I/O (that lives in ``scripts/orb_sip_study.py``).

Point-in-time discipline (no look-ahead):
  - ADV14 and Wilder ATR14 as of day D use only sessions strictly before D
    (``shift(1)`` of the rolling/Wilder series).
  - Opening-range relative volume compares D's first five 1-min RTH bars to the
    mean of that same window over the prior 14 sessions (missing sessions = 0;
    >= 10 of 14 sessions must have bars).
  - Selection is ranked at 09:35 on data available by then only.

Ambiguities in the public summaries, resolved conservatively here (2026-11-05):
  - Opening range requires the 09:30 bar to exist (it supplies the range open and
    the breakout reference). The range high/low and the direction close are taken
    over the window bars that are present; direction uses the close of the last
    present window bar (09:34 when it exists). A day missing the 09:30 bar is not
    traded.
  - Breakout/stop fills are worst-case: inside the entry bar the stop counts as hit
    if the bar's low (long) / high (short) crosses it, and a later bar that opens
    beyond the stop fills at that open (gap-through), not at the stop.
  - "Half the measured spread" secondary = commission (always charged) + half of the
    measured round-trip spread. "Commission only" = commission, no spread.
  - Ranking ties (equal RVOL) break by symbol ascending so selection is deterministic.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

# -- minute-of-day marks (ET; XNYS regular open 09:30) ------------------------
OPEN_MIN = 9 * 60 + 30            # 09:30
OR5_LAST_MIN = OPEN_MIN + 4       # 09:34  (five 1-min bars 09:30..09:34)
OR15_LAST_MIN = OPEN_MIN + 14     # 09:44  (fifteen 1-min bars, secondary)
ENTRY5_FIRST_MIN = OPEN_MIN + 5   # 09:35
ENTRY15_FIRST_MIN = OPEN_MIN + 15  # 09:45

# -- fixed rule parameters (prereg) -------------------------------------------
PRICE_MIN = 5.0
ADV_MIN = 1_000_000.0
ATR_MIN = 0.50
RVOL_MIN = 1.0
LOOKBACK = 14
RVOL_MIN_SESSIONS = 10
TOP_N = 20
STOP_ATR_MULT = 0.10
COMMISSION_PER_SHARE = 0.0035     # USD per share per side (the paper's)

# -- gate parameters (prereg) -------------------------------------------------
BOOT_SEED = 20261105
N_BOOT = 10_000
MIN_TRADES = 200
MONTH_SHARE_MAX = 0.50

_DAILY_COLS = ("symbol", "date", "open", "high", "low", "close", "volume")


# --------------------------------------------------------------------------- daily filters
def wilder_atr14(daily: pd.DataFrame) -> pd.Series:
    """Wilder 14-day ATR of one symbol's split-adjusted daily bars.

    ``daily`` has columns date/high/low/close, ascending, one symbol. The value at
    date t uses true ranges through t; the caller shifts by one to get the ATR
    "from the prior 14 sessions" as of a decision day. Returns a float Series
    indexed to ``daily.index``.
    """
    high = daily["high"].to_numpy(float)
    low = daily["low"].to_numpy(float)
    close = daily["close"].to_numpy(float)
    n = len(daily)
    out = np.full(n, np.nan)
    if n == 0:
        return pd.Series(out, index=daily.index, dtype=float)
    prev_close = np.concatenate(([np.nan], close[:-1]))
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    tr[0] = high[0] - low[0]
    if n >= LOOKBACK:
        out[LOOKBACK - 1] = float(np.mean(tr[:LOOKBACK]))
        for i in range(LOOKBACK, n):
            out[i] = (out[i - 1] * (LOOKBACK - 1) + tr[i]) / LOOKBACK
    return pd.Series(out, index=daily.index, dtype=float)


def build_daily_filters(daily: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, date): the official open, prior-14-session ADV and Wilder ATR14,
    and whether the day passes the three liquidity filters.

    ``daily`` columns: symbol, date, open, high, low, close, volume (split-adjusted).
    ``adv14``/``atr14`` are strictly point-in-time (prior sessions only). Returns a
    frame with symbol, date, open, adv14, atr14, passes.
    """
    missing = [c for c in _DAILY_COLS if c not in daily.columns]
    if missing:
        raise ValueError(f"daily frame missing columns {missing}")
    parts: list[pd.DataFrame] = []
    for sym, g in daily.sort_values(["symbol", "date"]).groupby("symbol", sort=False):
        g = g.reset_index(drop=True)
        adv = g["volume"].shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).mean()
        atr = wilder_atr14(g).shift(1)
        parts.append(pd.DataFrame({
            "symbol": sym, "date": g["date"].to_numpy(), "open": g["open"].to_numpy(float),
            "adv14": adv.to_numpy(float), "atr14": atr.to_numpy(float)}))
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["symbol", "date", "open", "adv14", "atr14"])
    passes = ((out["open"] > PRICE_MIN) & (out["adv14"] >= ADV_MIN) & (out["atr14"] > ATR_MIN))
    out["passes"] = passes.fillna(False).to_numpy(bool)
    return out


# --------------------------------------------------------------------------- opening range + RVOL
def aggregate_opening(open_bars: pd.DataFrame, last_min: int = OR5_LAST_MIN) -> pd.DataFrame:
    """Collapse the opening-window 1-min bars to one row per (symbol, date).

    ``open_bars`` columns: symbol, date, minute (minute-of-day ET), open, high, low,
    close, volume — only bars with OPEN_MIN <= minute <= ``last_min`` are used.
    Produces or_open (09:30 bar open), or_close (close of the last present window
    bar), or_high, or_low, vol5 (summed volume of the FIRST FIVE bars, for RVOL),
    nbars. A day without the 09:30 bar has or_open NaN and is untradeable.
    """
    b = open_bars[(open_bars["minute"] >= OPEN_MIN) & (open_bars["minute"] <= last_min)]
    rows: list[dict] = []
    for (sym, d), g in b.groupby(["symbol", "date"], sort=True):
        g = g.sort_values("minute")
        first = g[g["minute"] == OPEN_MIN]
        or_open = float(first["open"].iloc[0]) if len(first) else np.nan
        vol5 = float(g[g["minute"] <= OR5_LAST_MIN]["volume"].sum())
        rows.append({
            "symbol": sym, "date": d, "or_open": or_open,
            "or_close": float(g["close"].iloc[-1]), "or_high": float(g["high"].max()),
            "or_low": float(g["low"].min()), "vol5": vol5, "nbars": int(len(g))})
    cols = ["symbol", "date", "or_open", "or_close", "or_high", "or_low", "vol5", "nbars"]
    return pd.DataFrame(rows, columns=cols)


def opening_rvol(agg: pd.DataFrame, sessions: list) -> pd.DataFrame:
    """Opening relative volume per (symbol, date) over an ordered ``sessions`` list.

    vol5(D) / mean of vol5 over the prior 14 sessions, with sessions that have no
    bars counted as 0 and a requirement of >= 10 of the prior 14 with bars. Aligning
    on the full session calendar makes "missing = 0" exact and strictly point-in-time
    (``shift(1)``). Returns symbol, date, rvol, nsess (prior sessions with bars).
    """
    order = {d: i for i, d in enumerate(sessions)}
    parts: list[pd.DataFrame] = []
    for sym, g in agg.groupby("symbol", sort=False):
        s = g.set_index("date")["vol5"].reindex(sessions)
        present = s.notna()
        v0 = s.fillna(0.0)
        denom = v0.shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).sum() / LOOKBACK
        nsess = present.shift(1).rolling(LOOKBACK, min_periods=LOOKBACK).sum()
        ok = present & (nsess >= RVOL_MIN_SESSIONS) & (denom > 0)
        rvol = np.where(ok, s / denom, np.nan)
        part = pd.DataFrame({"symbol": sym, "date": sessions, "rvol": rvol,
                             "nsess": nsess.to_numpy(float)})
        part = part[present.to_numpy()]
        parts.append(part)
    out = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["symbol", "date", "rvol", "nsess"])
    _ = order
    return out


def select_in_play(day: pd.DataFrame, n: int = TOP_N) -> pd.DataFrame:
    """Top-``n`` stocks in play for one day: pass the daily filters, RVOL >= 1, ranked
    by RVOL descending with ties broken by symbol ascending (deterministic). Adds a
    1-based ``rank`` column. ``day`` is one date's merge of filters+RVOL+opening agg.
    """
    c = day[day["passes"] & (day["rvol"] >= RVOL_MIN) & day["rvol"].notna()].copy()
    c = c.sort_values(["rvol", "symbol"], ascending=[False, True]).head(n).reset_index(drop=True)
    c["rank"] = np.arange(1, len(c) + 1)
    return c


# --------------------------------------------------------------------------- trade simulation
def direction(or_open: float, or_close: float) -> int:
    """+1 long if the opening range closed up, -1 short if down, 0 (no trade) if flat
    or undefined."""
    if not (np.isfinite(or_open) and np.isfinite(or_close)):
        return 0
    if or_close > or_open:
        return 1
    if or_close < or_open:
        return -1
    return 0


def _gross(side: int, entry: float, exit_: float) -> float:
    return (exit_ - entry) / entry if side > 0 else (entry - exit_) / entry


def simulate(side: int, or_high: float, or_low: float, atr: float, bars: pd.DataFrame,
             entry_bar_stop: bool = True) -> dict | None:
    """Stop-order breakout with a 0.10xATR stop, one trade.

    ``bars`` are the day's 1-min RTH bars from the entry window start (09:35 for the
    5-min range) through the last RTH bar (15:59, or the early-close bar), ascending,
    columns open/high/low/close. Returns None if the breakout never triggers; else a
    dict with entry, exit, stopped, entry_i, exit_i and gross.
    """
    if side == 0 or not (np.isfinite(or_high) and np.isfinite(or_low) and np.isfinite(atr)) or atr <= 0:
        return None
    if len(bars) == 0:
        return None
    o = bars["open"].to_numpy(float)
    h = bars["high"].to_numpy(float)
    low = bars["low"].to_numpy(float)
    c = bars["close"].to_numpy(float)
    stop_dist = STOP_ATR_MULT * atr
    trig = or_high if side > 0 else or_low

    entry_i, entry = -1, np.nan
    for i in range(len(bars)):
        if side > 0 and h[i] >= trig:
            entry_i, entry = i, max(trig, o[i])      # gap-through: open above the stop level fills at the open
            break
        if side < 0 and low[i] <= trig:
            entry_i, entry = i, min(trig, o[i])
            break
    if entry_i < 0:
        return None
    # Stop 0.10 x ATR from the EXECUTED entry (prereg; review 2026-11-05: was measured from the trigger, too close
    # whenever the entry gapped through it).
    stop = entry - stop_dist if side > 0 else entry + stop_dist

    # Entry bar: worst-case stop (we cannot know intrabar order, so assume adverse). Amendment A1: the
    # ``entry_bar_stop=False`` secondary skips this check to bound the intrabar ambiguity.
    if not entry_bar_stop:
        pass
    elif side > 0 and low[entry_i] <= stop:
        return {"entry": entry, "exit": stop, "stopped": True, "entry_i": entry_i,
                "exit_i": entry_i, "gross": _gross(side, entry, stop)}
    if side < 0 and h[entry_i] >= stop:
        return {"entry": entry, "exit": stop, "stopped": True, "entry_i": entry_i,
                "exit_i": entry_i, "gross": _gross(side, entry, stop)}

    for j in range(entry_i + 1, len(bars)):
        if side > 0:
            if o[j] <= stop:                         # gapped through the stop at the open
                return {"entry": entry, "exit": o[j], "stopped": True, "entry_i": entry_i,
                        "exit_i": j, "gross": _gross(side, entry, o[j])}
            if low[j] <= stop:
                return {"entry": entry, "exit": stop, "stopped": True, "entry_i": entry_i,
                        "exit_i": j, "gross": _gross(side, entry, stop)}
        else:
            if o[j] >= stop:
                return {"entry": entry, "exit": o[j], "stopped": True, "entry_i": entry_i,
                        "exit_i": j, "gross": _gross(side, entry, o[j])}
            if h[j] >= stop:
                return {"entry": entry, "exit": stop, "stopped": True, "entry_i": entry_i,
                        "exit_i": j, "gross": _gross(side, entry, stop)}

    exit_ = c[-1]                                     # not stopped: exit at the last RTH bar's close
    return {"entry": entry, "exit": exit_, "stopped": False, "entry_i": entry_i,
            "exit_i": len(bars) - 1, "gross": _gross(side, entry, exit_)}


def cost_fractions(symbol: str, entry_ts: datetime, entry_price: float, adapter) -> dict:
    """Round-trip cost fractions of notional, one trade, priced at entry.

    ``adapter`` is a ``CostModelAdapter`` whose ``round_trip`` returns the full
    round-trip half-spread fraction (both sides, with the open-hour multiplier).
    Commission is $0.0035/share/side == COMMISSION_PER_SHARE/price per side.
    """
    if entry_price <= 0:
        raise ValueError("entry_price must be > 0")
    comm_rt = 2.0 * COMMISSION_PER_SHARE / entry_price
    spread_rt, _ = adapter.round_trip(symbol, entry_ts, entry_price)
    return {"primary": comm_rt + spread_rt,
            "commission_only": comm_rt,
            "half_spread": comm_rt + 0.5 * spread_rt}


def trade_record(symbol: str, d, side: int, rank: int, or_high: float, or_low: float,
                 or_open: float, atr: float, bars: pd.DataFrame, adapter) -> dict | None:
    """Full per-trade row (one name, one day) or None if no breakout. ``bars`` is the
    entry-through-close RTH frame with a ``ts`` column (UTC). Net returns subtract the
    primary and the two secondary cost variants once per round trip."""
    sim = simulate(side, or_high, or_low, atr, bars)
    if sim is None:
        return None
    free = simulate(side, or_high, or_low, atr, bars, entry_bar_stop=False)   # amendment A1 secondary
    entry_ts = pd.Timestamp(bars["ts"].iloc[sim["entry_i"]]).to_pydatetime()
    costs = cost_fractions(symbol, entry_ts, sim["entry"], adapter)
    gross = sim["gross"]
    return {
        "date": d, "symbol": symbol, "side": side, "rank": rank, "atr14": atr,
        "or_open": or_open, "or_high": or_high, "or_low": or_low,
        "entry": sim["entry"], "exit": sim["exit"], "stopped": bool(sim["stopped"]),
        "entry_ts": entry_ts, "gross": gross,
        "net_primary": gross - costs["primary"],
        "net_commission_only": gross - costs["commission_only"],
        "net_half_spread": gross - costs["half_spread"],
        "gross_entrybar_free": free["gross"], "net_commission_only_entrybar_free": free["gross"] - costs["commission_only"],
        "cost_primary": costs["primary"], "stop_dist": STOP_ATR_MULT * atr,
    }


# --------------------------------------------------------------------------- metrics / gate
def day_bootstrap_lower95(trades: pd.DataFrame, net_col: str, seed: int = BOOT_SEED,
                          n_boot: int = N_BOOT) -> float:
    """One-sided 95% lower bound on the mean net return under a day-cluster bootstrap
    (resample trading days with replacement; pooled mean = sum/count). Mirrors the
    convention in ``sgt.crypto.leadlag.gate``."""
    if len(trades) == 0:
        return float("nan")
    g = trades.groupby("date")[net_col].agg(["sum", "count"])
    s = g["sum"].to_numpy(float)
    cnt = g["count"].to_numpy(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(g), size=(n_boot, len(g)))
    means = s[idx].sum(1) / cnt[idx].sum(1)
    return float(np.quantile(means, 0.05))


def _share_block(trades: pd.DataFrame, net_col: str) -> dict:
    total = float(trades[net_col].sum())
    month = trades.groupby(pd.to_datetime(trades["date"]).dt.strftime("%Y-%m"))[net_col].sum()
    top_share = float(month.max() / total) if total > 0 else float("nan")
    return {"total": total, "top_month_share": top_share}


def summarize(trades: pd.DataFrame, halves_split, net_col: str = "net_primary",
              seed: int = BOOT_SEED) -> dict:
    """Prereg metric block over a trades frame (date, side, rank, gross, net_*).

    ``halves_split`` is the date at/after which the second half starts. Reports the
    primary gate plus the required secondaries (per cost variant mean, long/short,
    top5/10/20, hit, stopped share, per year, halves, top-month share). Pure.
    """
    n = len(trades)
    if n == 0:
        return {"n": 0, "pass": False}
    net = trades[net_col]
    lower = day_bootstrap_lower95(trades, net_col, seed)
    split = pd.Timestamp(halves_split)
    dt = pd.to_datetime(trades["date"])
    halves = (float(net[dt < split].mean()) if (dt < split).any() else float("nan"),
              float(net[dt >= split].mean()) if (dt >= split).any() else float("nan"))
    share = _share_block(trades, net_col)
    gate_ok = (n >= MIN_TRADES and float(net.mean()) > 0 and lower > 0
               and np.isfinite(halves[0]) and np.isfinite(halves[1]) and min(halves) > 0
               and share["total"] > 0 and share["top_month_share"] <= MONTH_SHARE_MAX)
    costs = {k: float(trades[f"net_{k}"].mean())
             for k in ("primary", "commission_only", "half_spread")}
    by_side = {int(s): {"n": int(len(g)), "mean_net": float(g[net_col].mean()),
                        "mean_gross": float(g["gross"].mean())}
               for s, g in trades.groupby("side")}
    tops = {f"top{k}": float(trades[trades["rank"] <= k][net_col].mean())
            for k in (5, 10, 20)}
    per_year = {str(y): {"n": int(len(g)), "mean_net": float(g[net_col].mean())}
                for y, g in trades.groupby(dt.dt.year)}
    return {
        "n": n, "mean_gross": float(trades["gross"].mean()), "mean_net": float(net.mean()),
        "lower95": lower, "hit": float((net > 0).mean()),
        "stopped_share": float(trades["stopped"].mean()), "halves": halves,
        "total_net": share["total"], "top_month_share": share["top_month_share"],
        "costs": costs, "by_side": by_side, "tops": tops, "per_year": per_year,
        "pass": bool(gate_ok),
    }


# --------------------------------------------------------------------------- portfolio sim
def portfolio_sim(trades: pd.DataFrame, net_col: str = "net_primary", start_capital: float = 25_000.0,
                  risk_frac: float = 0.01, leverage_cap: float = 4.0) -> dict:
    """The paper's sizing: 1% of capital at risk per trade (risk = 0.10xATR/share),
    total notional capped at 4x capital, $25k start, compounded day by day. Each day's
    trades are concurrent (entered 09:35+, out by the close). Returns final equity,
    total return and max daily drawdown."""
    if len(trades) == 0:
        return {"final_equity": start_capital, "total_return": 0.0, "max_dd": 0.0, "days": 0}
    cap = start_capital
    peak = start_capital
    max_dd = 0.0
    days = sorted(pd.to_datetime(trades["date"]).dt.date.unique())
    dt = pd.to_datetime(trades["date"]).dt.date
    for d in days:
        g = trades[dt.to_numpy() == d]
        stop_dist = g["stop_dist"].to_numpy(float)
        entry = g["entry"].to_numpy(float)
        ret = g[net_col].to_numpy(float)
        risk_dollars = risk_frac * cap
        shares = np.where(stop_dist > 0, risk_dollars / stop_dist, 0.0)
        notional = shares * entry
        gross_notional = float(notional.sum())
        scale = min(1.0, (leverage_cap * cap) / gross_notional) if gross_notional > 0 else 0.0
        pnl = float((notional * scale * ret).sum())
        cap += pnl
        peak = max(peak, cap)
        if peak > 0:
            max_dd = max(max_dd, (peak - cap) / peak)
    return {"final_equity": cap, "total_return": cap / start_capital - 1.0,
            "max_dd": max_dd, "days": len(days)}
