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


def trade_view(t: dict, bars: pd.DataFrame, usd: float | None = None, side: str = "short",
               pad_before: pd.Timedelta = PAD_BEFORE, pad_after: pd.Timedelta = PAD_AFTER) -> dict:
    """One trade's chart data. ``bars``: the session's RTH 1-minute bars (ts UTC, open, high, low, close, volume).
    ``side``: "short" (the fade, the scanner) or "long" (the live paper long book, owner 2026-11-10: longs first;
    the row carries its own stop/target and setup name)."""
    long = side == "long"
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
    lo, hi = alert - pad_before, (exited or entered) + pad_after
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
    if long:
        ret = official if official is not None else (exit_px / entry - 1 if entry and exit_px else None)
    else:
        ret = official if official is not None else (
            (entry - exit_px) / entry if entry and exit_px else None)
    open_k, close_k = ("buy", "sell") if long else ("short", "cover")
    markers = [{"time": _bar_at(times, alert), "kind": "alert", "text": "alert"},
               {"time": _bar_at(times, entered), "kind": open_k, "text": f"{open_k} {entry:.2f}" if entry else open_k}]
    if exited is not None:
        markers.append({"time": _bar_at(times, exited), "kind": close_k,
                        "text": f"{close_k} {exit_px:.2f}" if exit_px else close_k})
    return {
        "id": t.get("id"), "symbol": str(t["symbol"]), "session": str(t["session"]),
        "alert_type": str(t.get("alert_type") or ""), "status": str(t.get("status") or ""),
        "reason": str(t.get("reason") or ""), "alert_at": et_epoch(alert),
        "entry": entry, "exit": exit_px, "side": side, "setup": t.get("setup"),
        "stop": _num(t.get("stop")) if long else (entry * (1 + STOP) if entry else None),
        "target": _num(t.get("target")) if long else None,
        "ret": ret, "official": official is not None, "usd": usd,
        # owner note on CISS 2026-11-10: the candles stopped before the exit. Intraday the page only has bars to
        # about 16 minutes ago (SIP delay); the Notes page says so when the exit is past the last candle.
        "exit_at": et_epoch(exited) if exited is not None else None, "bars_end": times[-1] if times else None,
        "conf": t.get("conf"), "news": bool(t.get("news_own")), "headline": t.get("news_head"),
        "candles": candles, "volume": volume, "vwap": vwap, **ema, "rsi": rsi, "macd": macd, "signals": sig,
        "markers": [m for m in markers if m["time"] is not None],
    }


def book_view(e: dict, bars: pd.DataFrame, *, decided, entered, exited, timeframe: str, book: str,
              forecast: dict | None = None, tested: dict | None = None, score: dict | None = None) -> dict:
    """Chart data for one daily-book trade (sgt.ops.model_trades.episodes; owner 2026-11-14: every trade gets a card).

    ``bars``: 5-minute bars from the entry session to the exit (short holds) or daily bars (long holds); ``decided`` =
    when the order was written (15:50 ET), ``entered`` / ``exited`` = the closes it filled at. An open trade is shown
    at its last bar's close as an estimate; a closed one at the book's own fill prices."""
    side = e["side"]
    last = _num(bars.close.iloc[-1]) if len(bars) else None
    closed = e["status"] == "closed"
    row = {"id": e["id"], "symbol": e["symbol"], "session": e["entry_session"], "alert_type": "model",
           "status": e["status"], "posted_at": decided, "est_entry_at": entered, "est_entry": e["entry"],
           "est_exit": e["exit"] if closed else last, "est_exit_at": exited if closed else None,
           "reason": (f"closed at the {e['exit_session'][5:]} close" if closed else "open"), "setup": book}
    daily = timeframe == "1Day"
    v = trade_view(row, bars, None, side=side, pad_before=pd.Timedelta(days=60 if daily else 1),
                   pad_after=pd.Timedelta(days=7) if daily else PAD_AFTER)
    s = 1 if side == "long" else -1
    entry = _num(e["entry"])
    if closed:
        ret, usd, official = _num(e["net"]), _num(e["usd"]), True
    elif entry and last and _num(e.get("last_px")):
        ret, official = s * (last / entry - 1), False               # marked to the last bar: an estimate
        usd = (_num(e["usd"]) or 0.0) + _num(e["last_w"]) * float(e.get("nav") or 100_000.0) * (last / e["last_px"] - 1)
    else:
        ret, usd, official = None, None, False
    v.update(kind="model", book=book, strategy=e["strategy"], key=e["key"], timeframe=timeframe,
             weight=_num(e["weight"]), sessions=int(e["sessions"]), entry_session=e["entry_session"],
             exit_session=e["exit_session"], exit_scheduled=e.get("exit_scheduled"),
             ret=ret, usd=usd, official=official, stop=None, target=None, signals=None, conf=None,
             forecast=forecast, tested=tested, score=score)
    for m in v["markers"]:
        if m["kind"] == "alert":
            m["text"] = "decided"                                    # the 15:50 ET decision, not a channel alert
    if not closed:
        v["exit"] = last
    return v


LEARN_KEYS = ("challenger", "prereg", "n", "champ", "chal", "diff", "lower", "status")


def learning_rows(rows: list[dict] | None) -> list[dict]:
    """Scoreboard rows for the page (owner 2026-11-06: make the learning visible): known keys only, finite numbers,
    short strings (rendered with textContent)."""
    out = []
    for r in (rows or [])[:40]:
        x = {}
        for k in LEARN_KEYS:
            v = r.get(k)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                x[k] = float(v) if math.isfinite(float(v)) else None
            else:
                x[k] = str(v)[:80] if v is not None else None
        out.append(x)
    return out


def render(views: list[dict], generated: datetime, title: str = "SGT Fade Tape", standalone: bool = True,
           learning: list[dict] | None = None, models: dict | None = None, journal: list[dict] | None = None,
           scan_views: list[dict] | None = None, overview: dict | None = None,
           book_views: list[dict] | None = None) -> str:
    data = {"title": title, "generated": generated.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET"),
            "trades": views, "learning": learning_rows(learning), "models": models, "journal": journal or [],
            "scan_views": scan_views or [], "overview": overview, "book_views": book_views or []}
    payload = json.dumps(data, allow_nan=False).replace("<", "\\u003c")   # untrusted text can never end the script
    page = TEMPLATE.read_text().replace("/*TRADES*/null", payload, 1)
    return DOC_HEAD + page if standalone else page
