"""Trade charts (owner request 2026-11-05): every fade trade on its own 1-minute candles, TradingView style.

Builds one self-contained HTML page (TradingView Lightweight Charts, Apache-2.0, pinned from unpkg) with, per
trade: candles + volume, session VWAP, alert / short / cover markers, entry and stop lines, result, news and the
confidence that was shown. Pure: callers pass trades and bars; nothing here touches the network or the database.
Untrusted text (symbols, headlines) only ever reaches the page as JSON data rendered with textContent.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from sgt.fast import signals

ET = ZoneInfo("America/New_York")
TEMPLATE = Path(__file__).with_name("templates") / "trade_charts.html"
# The runner's chart server sends the page as is, so it needs its own document head (an Artifact publish adds one).
DOC_HEAD = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')
STOP = 0.20
PAD_BEFORE, PAD_AFTER = pd.Timedelta(minutes=45), pd.Timedelta(minutes=30)
MAX_BARS = 400                                       # one RTH session is 390 bars


def et_epoch(ts) -> int:
    """UTC instant -> seconds that a UTC-only chart axis prints as Eastern wall-clock time."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return int(t.timestamp() + t.tz_convert(ET).utcoffset().total_seconds())


def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _bar_at(times: list[int], when) -> int | None:
    """The chart time of the bar containing ``when`` (bars are labelled by their start minute)."""
    if when is None or not times:
        return None
    t = et_epoch(when) // 60 * 60
    best = None
    for x in times:
        if x <= t:
            best = x
    return best if best is not None else times[0]


def trade_view(t: dict, bars: pd.DataFrame, usd: float | None = None) -> dict:
    """One trade's chart data. ``bars``: the session's RTH 1-minute bars (ts UTC, open, high, low, close, volume)."""
    b = bars.copy()
    b["ts"] = pd.to_datetime(b.ts, utc=True)
    b = b.sort_values("ts").drop_duplicates("ts").tail(MAX_BARS)
    pv = (b.high + b.low + b.close) / 3 * b.volume
    b["vwap"] = pv.cumsum() / b.volume.cumsum().where(b.volume.cumsum() > 0)
    # EMA 9/21 for display (owner request 2026-11-05); as an exit they lose to the 60-min hold (prereg_fade_exits)
    b["ema9"] = b.close.ewm(span=9, adjust=False).mean()
    b["ema21"] = b.close.ewm(span=21, adjust=False).mean()
    official = _num(t.get("net"))
    settled = official is not None and _num(t.get("entry")) is not None and t.get("entry_ts")
    alert = pd.Timestamp(t["posted_at"])
    entered = pd.Timestamp(t["entry_ts"] if settled else (t.get("est_entry_at") or t["posted_at"]))
    exited = pd.Timestamp(t["est_exit_at"]) if t.get("est_exit_at") else None
    lo, hi = alert - PAD_BEFORE, (exited or entered) + PAD_AFTER
    mask = (b.ts >= lo) & (b.ts <= hi)
    w = b[mask]
    times = [et_epoch(x) for x in w.ts]
    candles = [{"time": tt, "open": _num(r.open), "high": _num(r.high), "low": _num(r.low), "close": _num(r.close)}
               for tt, r in zip(times, w.itertuples(index=False))]
    volume = [{"time": tt, "value": _num(r.volume) or 0.0, "up": bool(r.close >= r.open)}
              for tt, r in zip(times, w.itertuples(index=False))]
    vwap = [{"time": tt, "value": _num(v)} for tt, v in zip(times, w.vwap) if _num(v) is not None]
    ema = {k: [{"time": tt, "value": _num(v)} for tt, v in zip(times, w[k]) if _num(v) is not None]
           for k in ("ema9", "ema21")}
    # Tape oscillators (owner request 2026-11-05): causal RSI / MACD over the whole session, shown for the window.
    ser = signals.series(b)
    sel = {k: [v for v, keep in zip(ser[k], mask) if keep] for k in ser}
    # One point per candle (value None = whitespace) so the panes' bar indices line up with the main chart for sync.
    rsi = [{"time": tt, "value": v} for tt, v in zip(times, sel["rsi"])]
    macd = [{"time": tt, "line": m, "signal": s, "hist": h}
            for tt, m, s, h in zip(times, sel["macd"], sel["signal"], sel["hist"])]
    sig = signals.snapshot(b, entered)       # the day-trader read of the tape at the short entry
    # settled = the official SIP result at the tested timing; otherwise the live IEX estimate
    entry, exit_px = ((_num(t.get("entry")), _num(t.get("cover"))) if settled else
                      (_num(t.get("est_entry")), _num(t.get("est_exit"))))
    ret = official if official is not None else (
        (entry - exit_px) / entry if entry and exit_px else None)
    markers = [{"time": _bar_at(times, alert), "kind": "alert", "text": "alert"},
               {"time": _bar_at(times, entered), "kind": "short", "text": f"short {entry:.2f}" if entry else "short"}]
    if exited is not None:
        markers.append({"time": _bar_at(times, exited), "kind": "cover",
                        "text": f"cover {exit_px:.2f}" if exit_px else "cover"})
    return {
        "id": t.get("id"), "symbol": str(t["symbol"]), "session": str(t["session"]),
        "alert_type": str(t.get("alert_type") or ""), "status": str(t.get("status") or ""),
        "reason": str(t.get("reason") or ""), "alert_at": et_epoch(alert),
        "entry": entry, "exit": exit_px, "stop": entry * (1 + STOP) if entry else None,
        "ret": ret, "official": official is not None, "usd": usd,
        "conf": t.get("conf"), "news": bool(t.get("news_own")), "headline": t.get("news_head"),
        "candles": candles, "volume": volume, "vwap": vwap, **ema, "rsi": rsi, "macd": macd, "signals": sig,
        "markers": [m for m in markers if m["time"] is not None],
    }


def render(views: list[dict], generated: datetime, title: str = "SGT Fade Tape", standalone: bool = True) -> str:
    data = {"title": title, "generated": generated.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET"),
            "trades": views}
    payload = json.dumps(data, allow_nan=False).replace("<", "\\u003c")   # untrusted text can never end the script
    page = TEMPLATE.read_text().replace("/*TRADES*/null", payload, 1)
    return DOC_HEAD + page if standalone else page
