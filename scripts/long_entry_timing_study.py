"""When should the long book buy a base break? (docs/findings/long_entry_timing_2026-11-20.md)

Owner 2026-11-20: "the main thing was being able to get in at the right time, which is not something we are doing
optimally for LONGs". Exploration on the TRAINING events only (sessions before 2026-07-01); the later events are not
read. Two parts:

1. the live book's own base-break trades (``scripts/long_bot_replay.py`` output, live configuration): the same stop
   and exit rules with the buy moved in time on the official tape (when the break candle closes, at the feed's fill
   candle, one and two minutes later, and a buy-stop at the base top on these confirmed breaks: look-ahead);
2. every training event scanned the same way for two entries: the live rule (a close above the base, buy the next
   open) and a buy-stop resting at the base top with no close needed (no look-ahead).

    .venv/bin/python scripts/long_entry_timing_study.py --trades reports/long_bot_replay/history.csv --cfg new
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import long_bot_replay as br  # noqa: E402
import long_feed_replay_study as fr  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402

OUT = Path("reports/long_entry_timing")
ET = ZoneInfo("America/New_York")
LAST = time(15, 45)                    # the long book's last entry minute


def latest(session: str) -> pd.Timestamp:
    return pd.Timestamp(datetime.combine(date.fromisoformat(session), LAST, ET)).tz_convert("UTC")


def run(tape: pd.DataFrame, symbol: str, session: str, ready, trigger, stop: float, cost=br.cost_fn) -> dict | None:
    """One base-break order on the official tape with the live exits (stop, 2R, 30 minutes; no red-candle exit)."""
    t = pt.simulate(tape, pt.Setup("L8", "long", ready, trigger, stop), latest=latest(session))
    if t is None:
        return None
    gross = t.exit / t.entry - 1
    return {"entry": t.entry, "ts": t.entry_ts, "gross": gross,
            "net": gross - cost(symbol, t.entry_ts.to_pydatetime(), t.entry)}


def shifted(feed: pd.DataFrame, tape: pd.DataFrame, symbol: str, session: str, entry_ts, cost=br.cost_fn) -> dict:
    """The book's trade (its feed fill candle starts at ``entry_ts``) with the buy moved in time; same stop."""
    prev = feed[feed.ts < entry_ts]
    brk = prev.iloc[-1]                                    # the break candle: the feed's last candle before the fill
    ready = brk.ts + pt.BAR
    base = prev[(prev.ts >= brk.ts - pt.BASE_MIN) & (prev.ts < brk.ts)]
    top, low = float(base.high.max()), float(base.low.min())
    at = tape[tape.ts >= ready]
    out = {"wait_min": (entry_ts - ready).total_seconds() / 60, "base_top": top, "stop": low, "break_close": float(brk.close),
           "tape_at_close": float(at.iloc[0].open) if len(at) else np.nan}
    for name, when, trigger in (("at_close", ready, None), ("fill_candle", entry_ts, None), ("late1", entry_ts + pt.BAR, None),
                                ("late2", entry_ts + 2 * pt.BAR, None), ("buy_stop_confirmed", brk.ts, top)):
        x = run(tape, symbol, session, when, trigger, low, cost)
        out.update({f"{name}_{k}": (x[k] if x else np.nan) for k in ("entry", "gross", "net")})
    return out


def scan(feed: pd.DataFrame, tape: pd.DataFrame, symbol: str, session: str, start, cost=br.cost_fn, above: float = 0.0) -> dict:
    """Both entries found on the feed's closed candles from ``start`` and filled on the tape. ``c_``: the live rule.
    ``s_``: a buy-stop at the base top plus ``above`` dollars, placed for one minute at a time whenever the candles
    before that minute form a base whose top is above session VWAP; it fills in a minute whose tape high reaches it."""
    out: dict = {}
    s = pt.base_break(feed, start)
    if s:
        x = run(tape, symbol, session, s[0].ready, None, s[0].stop, cost)
        if x:
            out.update(c_net=x["net"], c_gross=x["gross"], c_entry=x["entry"], c_ts=x["ts"])
    ts, h, lo = feed.ts, feed.high.to_numpy(float), feed.low.to_numpy(float)
    vw, by_minute, last = pt.session_vwap(feed), tape.set_index("ts"), latest(session)
    for j in np.flatnonzero(((ts >= start + pt.BASE_MIN) & (ts <= start + pt.BASE_WATCH)).to_numpy()):
        a = int(ts.searchsorted(ts.iloc[j] - pt.BASE_MIN, side="left"))
        if j == 0 or j - a < pt.BASE_MIN_BARS:
            continue
        top, low = float(h[a:j].max()), float(lo[a:j].min())
        if not (low > 0 and (top - low) / low <= pt.BASE_MAX_RANGE and vw[j - 1] == vw[j - 1] and top > vw[j - 1]):
            continue
        m = ts.iloc[j]
        if m not in by_minute.index or m >= last:
            continue
        b = by_minute.loc[m]
        if b.open <= low or b.high < top + above:
            continue
        x = run(tape, symbol, session, m, top + above, low, cost)
        if x:
            out.update(s_net=x["net"], s_gross=x["gross"], s_entry=x["entry"], s_ts=x["ts"])
        break
    return out


def desc(x: pd.Series) -> dict:
    x = x.dropna()
    return {"n": int(len(x)), "mean": float(x.mean()) if len(x) else None, "win": float((x > 0).mean()) if len(x) else None}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", type=Path, required=True, help="long_bot_replay.py output (a csv with cfg and split)")
    ap.add_argument("--cfg", default="new")
    a = ap.parse_args(argv)
    from sgt.intraday.store import ParquetBarStore

    store = ParquetBarStore("data/alpaca")

    def tape_of(symbol: str, session: str) -> pd.DataFrame:
        d = date.fromisoformat(session)
        return br.frame(store.read_frame(symbol, d, d))

    t = pd.read_csv(a.trades, converters={"symbol": str})
    t = t[(t.cfg == a.cfg) & (t.status == "settled") & (t.split == "train") & (t.kind == "L8")]      # training only
    one = pd.DataFrame([{"symbol": r.symbol, "session": str(r.session), "book_net": r.net, "book_gross": r.gross, "feed_entry": r.est_entry,
                         **shifted(br.frame(fr.feed_bars(r.symbol, str(r.session))), tape_of(r.symbol, str(r.session)),
                                   r.symbol, str(r.session), pd.Timestamp(r.entry_ts))} for r in t.itertuples()])
    two = []
    for e in fr.events("train").itertuples(index=False):
        feed = fr.feed_bars(e.symbol, e.session)
        tape = tape_of(e.symbol, e.session) if feed is not None and len(feed) else pd.DataFrame()
        if len(tape):
            f = br.frame(feed)
            cent = scan(f, tape, e.symbol, e.session, e.t0, above=0.01)       # the same order one cent above the top
            two.append({"symbol": e.symbol, "session": e.session, **scan(f, tape, e.symbol, e.session, e.t0),
                        "s1_net": cent.get("s_net"), "s1_entry": cent.get("s_entry")})
    two = pd.DataFrame(two).reindex(columns=["symbol", "session", "c_net", "c_gross", "c_entry", "c_ts", "s_net", "s_gross",
                                             "s_entry", "s_ts", "s1_net", "s1_entry"]).astype({"s1_net": float})
    both = one.dropna(subset=["at_close_net", "late1_net"])
    res = {"doc": "docs/findings/long_entry_timing_2026-11-20.md", "split": "train",
           "book_trades": {"n": len(one), "book": desc(one.book_net), "book_gross": float(one.book_gross.mean()),
                           **{k: {**desc(one[f"{k}_net"]), "gross": float(one[f"{k}_gross"].mean())}
                              for k in ("at_close", "fill_candle", "late1", "late2", "buy_stop_confirmed")},
                           "late1_minus_at_close": float((both.late1_net - both.at_close_net).mean()), "n_both": len(both),
                           "feed_waited": int((one.wait_min > 0).sum()),
                           "abs_off_tape": {"break_close": float((one.break_close / one.tape_at_close - 1).abs().mean()),
                                            "feed_next_open": float((one.feed_entry / one.tape_at_close - 1).abs().mean())}},
           "scan": {"events": len(two), "close_rule": {**desc(two.c_net), "per_event": float(two.c_net.fillna(0).mean())},
                    "buy_stop": {**desc(two.s_net), "per_event": float(two.s_net.fillna(0).mean())},
                    "buy_stop_only": desc(two[two.c_net.isna()].s_net), "close_rule_only": desc(two[two.s_net.isna()].c_net),
                    "buy_stop_cent_above": {**desc(two.s1_net), "per_event": float(two.s1_net.fillna(0).mean())},
                    "buy_stop_cent_above_only": desc(two[two.c_net.isna()].s1_net)}}
    OUT.mkdir(parents=True, exist_ok=True)
    one.to_csv(OUT / "book_trades.csv", index=False)
    two.to_csv(OUT / "scan.csv", index=False)
    (OUT / "result.json").write_text(json.dumps(res, indent=2, default=str) + "\n")
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
