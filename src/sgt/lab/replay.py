"""Replay lab: run the system's own rules over one symbol-session of 1-minute bars and return everything they saw
and did, ready to draw (owner 2026-11-17: "pick a random ticker ... where we would buy and sell long, short ... what it
recognizes as a bull flag, break of structure ... see it visually very clearly ... see what's going wrong").

Nothing here is a new rule. Every detection and every trade comes from the module the books run:
``sgt.fast.exits`` (the fade: 20% stop, 60 minutes), ``sgt.fast.long_shadow.setups`` + ``sgt.fast.patterns.simulate``
(the long setups, with the live book's red-candle exit), ``sgt.fast.bullflag`` (the owner's Clean Bull Flag Finder),
``sgt.fast.structure`` (break of structure / change of character), ``sgt.fast.spikes`` (the scanner's spike and drop).
So a chart that looks wrong in the lab is the system being wrong, not the lab.

Pure: bars in, a JSON-safe dict out. No network, no database. ``bars``: one session's regular-hours 1-minute bars,
``ts`` = bar start (UTC), open/high/low/close/volume, sorted; minutes without a trade have no bar. A replay of the
first k bars equals the first part of the replay of all bars for everything decided by then (tested): the lab cannot
show a detection earlier than the live system could have made it.

What the lab does NOT know: the live filters that need outside data (company news, beta, a repeat alert in the last 7
days, shares to borrow). Each trade here is the rule as tested on the tape; a caller that knows what the live book did
passes it in ``live`` and it is shown next to the replay.
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd

from sgt.fast import bullflag, est_costs, exits, fade, long_shadow, spikes, structure
from sgt.fast import patterns as pt
from sgt.ops.charts import et_epoch, finite

ET = "America/New_York"
HALT_GAP = 5                       # minutes without a bar inside the session shown as a gap (halt or no trading)
MAX_BARS = 500                     # one regular session is 390 bars; more is not one session
MAX_ALERTS = 40
MAX_TEXT = 40
RULES = {"fade": "Short the alert (20% stop, 60 min)", "scan": "Short our own spike (paused rule)",
         "L4a": "Long: first red candle, buy next open", "L4b": "Long: first red candle, buy break of its high",
         "L5v2": "Long: bull flag breakout", "S2v2": "Short: bull flag fails", "L5": "Long: bull flag breakout (v1)",
         "S2": "Short: bull flag fails (v1)", "L6": "Long: break of structure",
         "L7a": "Long: first red candle after a halt, buy next open",
         "L7b": "Long: first red candle after a halt, buy break of its high",
         "L8": "Long: base break (some minutes in a narrow range, then a close above it and above VWAP)"}
EXIT_WORDS = {"stop": "stop", "target": "target", "time": "time limit", "red": "fill candle closed red",
              "cap": "60 minutes", "signal": "signal"}


def _t(ts) -> int:
    return et_epoch(ts)


def _hm(ts) -> str:
    return pd.Timestamp(ts).tz_convert(ET).strftime("%H:%M")


def _clean(bars: pd.DataFrame) -> pd.DataFrame:
    """Bars as the rule modules expect them; raises on anything that is not one session of sane bars."""
    need = ["ts", "open", "high", "low", "close", "volume"]
    if any(c not in bars for c in need):
        raise ValueError("bars need ts, open, high, low, close, volume")
    f = bars[need].copy()
    f["ts"] = pd.to_datetime(f.ts, utc=True)
    f = f.dropna().drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    if len(f) > MAX_BARS:
        raise ValueError(f"{len(f)} bars: more than one session")
    px = f[["open", "high", "low", "close"]].to_numpy(float)
    if len(f) and (not np.isfinite(px).all() or (px <= 0).any() or (f.high < f.low).any()):
        raise ValueError("bars hold a non-positive or inconsistent price")
    return f


def _halts(f: pd.DataFrame) -> list[dict]:
    gap = f.ts.diff().dt.total_seconds().div(60).fillna(1)
    return [{"from": _t(f.ts[i - 1]), "to": _t(f.ts[i]), "minutes": int(gap[i]) - 1,
             "text": f"no trades {_hm(f.ts[i - 1])} to {_hm(f.ts[i])} ({int(gap[i]) - 1} min): halt or no prints"}
            for i in range(1, len(f)) if gap[i] - 1 >= HALT_GAP]


def _lines(f: pd.DataFrame) -> dict:
    ind = exits.indicators(f)
    pts = lambda col: [{"time": _t(t), "value": float(v)} for t, v in zip(f.ts, ind[col]) if np.isfinite(v)]  # noqa: E731
    return {"ema9": pts("ema9"), "ema21": pts("ema21"), "vwap": pts("vwap")}


def _flags(f: pd.DataFrame) -> list[dict]:
    """The owner's Clean Bull Flag Finder over the whole session: one entry per breakout it would plot."""
    out = []
    for s in bullflag.find_signals(f):
        a, p = s.i - s.fb, s.i - s.fb - s.pb
        out.append({"time": _t(f.ts[s.i]), "flag_from": _t(f.ts[a]), "flag_to": _t(f.ts[s.i - 1]),
                    "pole_from": _t(f.ts[p]), "flag_high": s.flag_high, "flag_low": s.flag_low,
                    "pole_high": s.pole_high, "pole_low": s.pole_low, "strength": round(float(s.strength), 1),
                    "up_move_pct": round(float(s.up_move_pct), 2), "retrace": round(float(s.retrace), 3),
                    "text": f"bull flag breakout {_hm(f.ts[s.i])}: pole +{s.up_move_pct:.1f}%, pullback "
                            f"{s.retrace:.0%} of it, strength {s.strength:.0f}"})
    return out


def _structure(f: pd.DataFrame) -> dict:
    """Swing pivots and bullish breaks (the owner's script's rule): a pivot is drawn at its bar but was only KNOWN
    ``structure.LEN`` bars later (``known``), which is when a rule could first use it."""
    high, low = f.high.to_numpy(float), f.low.to_numpy(float)
    ph, pl = structure.pivots(high, low)
    n, k = len(f), structure.LEN
    piv = [{"time": _t(f.ts[j]), "price": float(high[j] if up else low[j]), "kind": "high" if up else "low",
            "known": _t(f.ts[j + k])}
           for j in range(n) for up in (True, False) if (ph[j] if up else pl[j]) and j + k < n]
    brk = [{"time": _t(f.ts[b.i]), "kind": b.kind, "level": float(b.level), "stop": b.stop,
            "text": f"{'break of structure' if b.kind == 'BOS' else 'change of character'} {_hm(f.ts[b.i])}: "
                    f"close above {b.level:.4g}"}
           for b in structure.bullish_breaks(f, owner=True)]
    return {"pivots": piv, "breaks": brk}


def _spikes(f: pd.DataFrame) -> list[dict]:
    out = []
    for kind, fn in (("spike", spikes.detect), ("drop", spikes.detect_drop)):
        i = fn(f)
        if i is not None:
            out.append({"time": _t(f.ts[i]), "kind": kind, "i": int(i),
                        "text": f"scanner {kind} {_hm(f.ts[i])}: 5% in 5 min on 5x volume"})
    return out


def _trade(rule: str, side: str, start: str, *, symbol: str, entry_ts, entry: float, exit_ts, exit_px: float,
           reason: str, gross: float, stop: float | None, target: float | None, ready=None, trigger=None) -> dict:
    entry, gross = float(entry), float(gross)
    cost = est_costs.cost(side, symbol, pd.Timestamp(entry_ts).to_pydatetime(), entry)
    cost = None if cost is None else float(cost)
    net = None if cost is None else gross - cost
    num = lambda x: None if x is None else float(x)  # noqa: E731
    return {"rule": rule, "name": RULES.get(rule, rule), "side": side, "start": start,
            "ready": None if ready is None else _t(ready), "trigger": num(trigger), "stop": num(stop), "target": num(target),
            "entry_time": _t(entry_ts), "entry_hm": _hm(entry_ts), "entry": float(entry),
            "exit_time": _t(exit_ts), "exit_hm": _hm(exit_ts), "exit": float(exit_px),
            "reason": EXIT_WORDS.get(reason, reason), "gross": float(gross), "cost": cost, "net": net,
            "minutes": int((pd.Timestamp(exit_ts) - pd.Timestamp(entry_ts)).total_seconds() // 60), "book": False}


def _fade(f: pd.DataFrame, ind: pd.DataFrame, symbol: str, session: str, posted: pd.Timestamp, label: str,
          rule: str = "fade") -> tuple[dict | None, str | None]:
    """The fade's tested trade for one alert, or why there is none."""
    nxt = f[f.ts >= posted + pd.Timedelta(fade.REACTION)]
    if nxt.empty:
        return None, "no bar after the alert"
    t0, px = nxt.ts.iloc[0], float(nxt.open.iloc[0])
    if t0 - posted > pd.Timedelta(fade.MAX_ENTRY_DELAY):
        return None, f"no bar within 5 minutes of the alert (next print {_hm(t0)}): halted, not shorted"
    et = t0.tz_convert(ET)
    if not fade.FIRST_ENTRY <= et.time() <= fade.last_entry(date.fromisoformat(session)):
        return None, f"entry {_hm(t0)} is outside the rule's hours"
    if not fade.PRICE_MIN <= px <= fade.PRICE_MAX:
        return None, f"price {px:.4g} is outside $1 to $50"
    s = exits.simulate(ind, t0, px, exits.CHAMPION)
    if s is None:
        return None, "no bars to hold"
    i0 = int(np.flatnonzero((ind.ts >= t0).to_numpy())[0])
    exit_ts = ind.ts.iloc[min(i0 + s["minutes"] - 1, len(ind) - 1)]     # ``minutes`` counts BARS held (gaps have none)
    exit_px = px * (1 - s["gross"])
    return _trade(rule, "short", label, symbol=symbol, entry_ts=t0, entry=px, exit_ts=exit_ts, exit_px=exit_px,
                  reason=s["how"], gross=s["gross"], stop=px * (1 + exits.STOP), target=None), None


def _longs(f: pd.DataFrame, symbol: str, session: str, posted: pd.Timestamp, label: str) -> tuple[list[dict], list[dict]]:
    """Every long setup the books look for after this alert, simulated as the live long book exits them (one-minute
    setups: red fill candle out at the next open; the base break: stop, target or time only). Returns (trades, setups
    that never filled)."""
    from sgt.fast import long_book

    found, t0 = long_shadow.setups(f, {"posted_at": posted.isoformat(), "status": "skipped"})
    if t0 is not None:
        found = found + pt.base_break(f, t0)                 # 2026-11-19: the live long book's entry
    trades, idle = [], []
    latest = long_shadow._latest(session)
    for s in found:
        t = pt.simulate(f, s, latest=latest, red_exit=s.side == "long" and s.kind != "L8")
        if t is None:
            idle.append({"rule": s.kind, "name": RULES.get(s.kind, s.kind), "side": s.side, "start": label,
                         "ready": _t(s.ready), "trigger": s.trigger, "stop": s.stop,
                         "text": f"{RULES.get(s.kind, s.kind)}: set up at {_hm(s.ready)} but never filled "
                                 f"(not triggered within {pt.TRIGGER_BARS} minutes, or cancelled at its stop)"})
            continue
        risk = abs(t.entry - s.stop)
        target = t.entry + pt.RR * risk if s.side == "long" else t.entry - pt.RR * risk
        trades.append(_trade(s.kind, s.side, label, symbol=symbol, entry_ts=t.entry_ts, entry=t.entry,
                             exit_ts=t.exit_ts, exit_px=t.exit, reason=t.reason, gross=t.gross, stop=s.stop,
                             target=target, ready=s.ready, trigger=s.trigger))
    longs = [x for x in trades if x["side"] == "long"]
    for x in longs:                                          # 2026-11-17: a target that cannot beat costs is not taken
        x["nowin"] = x["cost"] is not None and x["target"] is not None and \
            (x["target"] - x["entry"]) / x["entry"] <= x["cost"]
        if x["nowin"]:
            x["name"] += " (target cannot beat costs: the book skips it)"
    can = [x for x in longs if not x["nowin"] and x["rule"] in long_book.LIVE_KINDS]
    if can:                                                  # the live long book takes the first of ITS setups that can win
        first = min(can, key=lambda x: x["entry_time"])
        if first["cost"] is not None and first["cost"] > long_book.COST_CAP:    # owner 2026-11-20: not bought
            first["name"] += f" (cost above {long_book.COST_CAP:.0%}: the book does not buy this alert's long)"
        else:
            first["book"] = True
    return trades, idle


def _alert_time(a: dict) -> pd.Timestamp | None:
    try:
        t = pd.Timestamp(a.get("at"))
    except (TypeError, ValueError):
        return None
    if t is pd.NaT:
        return None
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def replay(bars: pd.DataFrame, symbol: str, session: str, alerts: list[dict] | None = None,
           live: dict | None = None) -> dict:
    """One symbol-session replayed. ``alerts``: channel alerts of that day, each ``{"at": UTC instant, "type": str}``
    (the first one in the tested cells starts the trades, as the rule trades the first event of a symbol-day). With
    no alert, the scanner's own spike (if any) is the start, labelled as such. ``live``: free-form text of what the
    live books did (shown, never used)."""
    f = _clean(bars)
    sym = str(symbol)[:12]
    out = {"symbol": sym, "session": str(session)[:10], "bars": len(f), "live": live or None,
           "candles": [{"time": _t(r.ts), "open": float(r.open), "high": float(r.high), "low": float(r.low),
                        "close": float(r.close), "volume": float(r.volume)} for r in f.itertuples()],
           "lines": {}, "halts": [], "flags": [], "structure": {"pivots": [], "breaks": []}, "spikes": [],
           "alerts": [], "trades": [], "idle": [], "notes": []}
    if f.empty:
        out["notes"].append("no bars for this session")
        return finite(out)
    out["from"], out["to"] = _hm(f.ts.iloc[0]), _hm(f.ts.iloc[-1])
    ind = exits.indicators(f)
    out["lines"], out["halts"], out["flags"] = _lines(f), _halts(f), _flags(f)
    out["structure"], out["spikes"] = _structure(f), _spikes(f)

    started = False
    for a in sorted((x for x in (alerts or [])[:MAX_ALERTS] if _alert_time(x) is not None), key=_alert_time):
        at, kind = _alert_time(a), str(a.get("type") or "alert")[:MAX_TEXT]
        label = f"alert {_hm(at)} ({kind.replace('_', ' ')})"
        row = {"time": _t(at), "type": kind, "text": label, "traded": False}
        out["alerts"].append(row)
        if started:
            row["why"] = "later alert of the same stock that day: the rule trades the first one only"
            continue
        if kind not in fade.CELLS:
            row["why"] = "alert type is not one the rule trades"
            continue
        # As fade.on_alert: an alert whose tested entry minute is outside the rule's hours is filtered and does NOT
        # use up the stock's one event of the day (2026-11-17: a pre-market alert on GP hid its 09:30 trades).
        last = fade.last_entry(date.fromisoformat(out["session"]))
        if not fade.FIRST_ENTRY <= fade.entry_start(at.to_pydatetime()).astimezone(fade.ET).time() <= last:
            row["why"] = f"outside the rule's hours (09:30 to {last:%H:%M} ET)"
            continue
        started = row["traded"] = True
        short, why = _fade(f, ind, sym, out["session"], at, label)
        if short:
            short["book"] = True
            out["trades"].append(short)
        else:
            out["notes"].append(f"short: {why}")
        trades, idle = _longs(f, sym, out["session"], at, label)
        for x in trades if short else []:                   # owner 2026-11-20: one account, one side
            if x["book"] and short["entry_time"] <= x["entry_time"] < short["exit_time"]:
                x["book"] = False
                x["name"] += " (our short on this stock is open: the book does not buy)"
        out["trades"] += trades
        out["idle"] += idle
        if not trades and not idle:
            out["notes"].append("long: no setup formed in the 30 minutes after the alert")
    if not started:
        spike = next((s for s in out["spikes"] if s["kind"] == "spike"), None)
        if spike is None:
            out["notes"].append("no channel alert and no spike of our own: nothing would have traded; the chart "
                                "shows what the detectors saw")
        else:                                               # the scanner's rule: read at the close, act next bar
            posted = f.ts[spike["i"]] + pt.BAR - pd.Timedelta(fade.REACTION)
            label = f"our spike {_hm(f.ts[spike['i']])}"
            short, why = _fade(f, ind, sym, out["session"], posted, label, rule="scan")
            if short:
                out["trades"].append(short)
            else:
                out["notes"].append(f"scanner short: {why}")
            trades, idle = _longs(f, sym, out["session"], posted, label)
            out["trades"] += trades
            out["idle"] += idle
    out["trades"].sort(key=lambda x: (x["entry_time"], x["rule"]))
    out["timeline"] = timeline(out)
    nets = [x["net"] for x in out["trades"] if x["net"] is not None]
    out["summary"] = {"trades": len(out["trades"]), "wins": int(sum(n > 0 for n in nets)),
                      "worst": min(nets) if nets else None, "best": max(nets) if nets else None,
                      "flags": len(out["flags"]), "breaks": len(out["structure"]["breaks"])}
    return finite(out)


def timeline(r: dict) -> list[dict]:
    """What the rules saw and did, in time order, in plain words (the page's "minute by minute" list)."""
    ev = [{"time": a["time"], "kind": "alert", "text": a["text"] + ("" if a["traded"] else f": {a.get('why', '')}")}
          for a in r["alerts"]]
    ev += [{"time": h["from"], "kind": "halt", "text": h["text"]} for h in r["halts"]]
    ev += [{"time": s["time"], "kind": "spike", "text": s["text"]} for s in r["spikes"]]
    ev += [{"time": x["time"], "kind": "flag", "text": x["text"]} for x in r["flags"]]
    ev += [{"time": b["time"], "kind": "break", "text": b["text"]} for b in r["structure"]["breaks"]]
    ev += [{"time": i["ready"], "kind": "idle", "text": i["text"]} for i in r["idle"]]
    for x in r["trades"]:
        verb = "short" if x["side"] == "short" else "buy"
        extra = f", stop {x['stop']:.4g}" + (f", target {x['target']:.4g}" if x["target"] else "")
        ev.append({"time": x["entry_time"], "kind": "entry",
                   "text": f"{x['name']}: {verb} {x['entry']:.4g} at {x['entry_hm']}{extra}"})
        res = "" if x["net"] is None else f" = {x['net']:+.1%} after costs"
        ev.append({"time": x["exit_time"], "kind": "exit",
                   "text": f"{x['name']}: out {x['exit']:.4g} at {x['exit_hm']} ({x['reason']}){res}"})
    return sorted(ev, key=lambda e: (e["time"], {"alert": 0, "halt": 1, "entry": 3, "exit": 4}.get(e["kind"], 2)))


def stamp(generated: datetime) -> str:
    return generated.astimezone(fade.ET).strftime("%a %b %-d, %-I:%M %p ET")
