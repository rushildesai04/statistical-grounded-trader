"""Where do the registered binary comparisons (``binary-v0``) and the exact decimal ones (``price-decimal-v1``,
``sgt.fast.exact_price``) decide differently on the stored history?

For the amendment ``docs/findings/amendment_stop_arithmetic_2026-11-25.md``. READ-ONLY: the ops database is opened
read-only, the bar store and the ledger are only read, nothing is written and no result is changed. It prints COUNTS
of comparisons and of trades whose decision differs, and for each such trade BOTH outcomes. It never ranks, averages
or selects by return: the question is arithmetic, not performance.

Populations (each named; none is pooled with another):
  P1  settled live shorts: the registered replay's stop, from the settled entry, over its 60 minutes of official bars
  P2  the same trades at the LIVE entry price: the level the daytime official-tape check applies
  P3  every alert row with stored official bars: the forward record's entry (open of the first regular-hours minute
      at or after alert + 10 s, within 5 minutes) and its stop
  P4  settled longs: the target (entry + 2 x risk) from the settled entry over 30 minutes
  P5  ledger rows: the short-sale price test (a low at or under 0.9 x a prior close), today's and the carried one
  P6  the nightly long shadow (every setup of every alert, no orders): the same target comparison, long setups
NOT covered: the execution model's own thresholds against the tick tape (it needs the paid tape again; its history
run is frozen), and the research history files (not re-read: they are single-run studies).

Run on the runner:  python scripts/stop_arithmetic_audit.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src")) if "__file__" in globals() else None
try:
    from sgt.fast import exact_price as xp
except ImportError:                                # run from a pasted copy that defines the module above this code
    xp = sys.modules["__main__"]

OPS_DB, BARS, LEDGER = "reports/ops/ops.db", Path("data/fast"), Path("reports/exec_model/ledger.jsonl")
REACTION, MAX_DELAY, SHORT_HOLD, LONG_HOLD = (pd.Timedelta(seconds=10), pd.Timedelta(minutes=5), pd.Timedelta(minutes=60),
                                              pd.Timedelta(minutes=30))


class Tally:
    def __init__(self, name: str) -> None:
        self.name, self.trades, self.no_bars, self.bars, self.differ_bars = name, 0, 0, 0, 0
        self.not_prices, self.touch, self.differ = 0, 0, []
        self.places: Counter = Counter()

    def compare(self, label: str, pairs) -> None:
        """``pairs``: (bar time, reported price, binary decision, decimal decision or None when not a price)."""
        self.trades += 1
        first_b = first_d = None
        for ts, px, b, d in pairs:
            self.bars += 1
            if d is None:
                self.not_prices += 1
                continue
            self.places[max(0, -xp.price(px).as_tuple().exponent)] += 1
            if b != d:
                self.differ_bars += 1
            first_b = ts if b and first_b is None else first_b
            first_d = ts if d and first_d is None else first_d
        if first_b != first_d:
            self.differ.append({"trade": label, "binary-v0 first reached": str(first_b), "price-decimal-v1 first reached": str(first_d)})

    def line(self) -> str:
        dp = ", ".join(f"{k} dp: {v}" for k, v in sorted(self.places.items()))
        return (f"{self.name}: {self.trades} trades compared ({self.no_bars} without stored bars), {self.bars} bar "
                f"comparisons, {self.differ_bars} bars decided differently, {len(self.differ)} trades decided differently"
                f"; values refused as not a price: {self.not_prices}; exact touches of the level: {self.touch}"
                f"; decimal places of the prices compared: {dp or 'none'}")


def _dec(fn, *a):
    try:
        return fn(*a)
    except (xp.NotAPrice, TypeError):
        return None


def rth(store, symbol: str, session: str) -> pd.DataFrame | None:
    d = date.fromisoformat(session)
    try:
        f = store.read_frame(symbol, d, d)
    except Exception:  # noqa: BLE001 - a missing or unreadable day is counted, never guessed
        return None
    if f is None or f.empty:
        return None
    f = f.assign(ts=pd.to_datetime(f.ts, utc=True))
    return f[f.segment == "rth"].sort_values("ts").reset_index(drop=True)


def short_pairs(t: Tally, w: pd.DataFrame, entry: float):
    lv = _dec(xp.level_above, entry)
    for r in w.itertuples(index=False):
        d = _dec(xp.decimal_short_stop, entry, r.high)
        if d is not None and lv is not None and xp.price(r.high) == lv:
            t.touch += 1
        yield r.ts, r.high, xp.binary_short_stop(float(entry), float(r.high)), d


def main() -> int:
    from sgt.intraday.store import ParquetBarStore

    c = sqlite3.connect(f"file:{OPS_DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    store = ParquetBarStore(BARS)
    p1, p2, p3, p4, p5, p6 = (Tally("P1 settled shorts, registered replay"), Tally("P2 settled shorts, live entry level"),
                              Tally("P3 every alert, forward-record entry"), Tally("P4 settled longs, target"),
                              Tally("P5 ledger, price test"), Tally("P6 long shadow, target"))
    for r in c.execute("SELECT id, symbol, session, entry_ts, entry, est_entry, est_entry_at, est_exit_at FROM fade_trades "
                       "WHERE status='settled' AND entry IS NOT NULL ORDER BY id"):
        f = rth(store, r["symbol"], r["session"])
        if f is None:
            p1.no_bars += 1
            continue
        t0 = pd.Timestamp(r["entry_ts"])
        p1.compare(f"short {r['id']} {r['symbol']} {r['session']}", short_pairs(p1, f[(f.ts >= t0) & (f.ts < t0 + SHORT_HOLD)], r["entry"]))
        if r["est_entry"] and r["est_entry_at"]:
            a = pd.Timestamp(r["est_entry_at"]).floor("min") + pd.Timedelta(minutes=1)
            b = pd.Timestamp(r["est_exit_at"]) if r["est_exit_at"] else a + SHORT_HOLD
            p2.compare(f"short {r['id']} {r['symbol']} {r['session']}", short_pairs(p2, f[(f.ts >= a) & (f.ts < b)], r["est_entry"]))
    seen = set()
    for r in c.execute("SELECT id, symbol, session, posted_at FROM fade_trades ORDER BY id"):
        if (r["symbol"], r["session"]) in seen:                # the forward record takes the first alert of a stock-day
            continue
        seen.add((r["symbol"], r["session"]))
        f = rth(store, r["symbol"], r["session"])
        if f is None:
            p3.no_bars += 1
            continue
        at = pd.Timestamp(r["posted_at"]) + REACTION
        after = f[f.ts >= at]
        if after.empty or after.ts.iloc[0] - pd.Timestamp(r["posted_at"]) > MAX_DELAY:
            continue
        t0, entry = after.ts.iloc[0], float(after.open.iloc[0])
        p3.compare(f"alert {r['id']} {r['symbol']} {r['session']}", short_pairs(p3, f[(f.ts >= t0) & (f.ts < t0 + SHORT_HOLD)], entry))
    for r in c.execute("SELECT id, symbol, session, fill_ts, entry, stop FROM long_trades WHERE status='settled' AND entry "
                       "IS NOT NULL AND stop IS NOT NULL AND fill_ts IS NOT NULL ORDER BY id"):
        f = rth(store, r["symbol"], r["session"])
        if f is None:
            p4.no_bars += 1
            continue
        t0 = pd.Timestamp(r["fill_ts"])
        w = f[(f.ts >= t0) & (f.ts < t0 + LONG_HOLD)]
        tg = _dec(xp.long_target, r["entry"], r["stop"])
        pairs = []
        for b in w.itertuples(index=False):
            d = _dec(xp.decimal_long_target, r["entry"], r["stop"], b.high)
            p4.touch += d is not None and tg is not None and xp.price(b.high) == tg
            pairs.append((b.ts, b.high, xp.binary_long_target(float(r["entry"]), float(r["stop"]), float(b.high)), d))
        p4.compare(f"long {r['id']} {r['symbol']} {r['session']}", pairs)
    for r in c.execute("SELECT id, symbol, session, kind, entry_ts, entry, stop FROM long_shadow WHERE side='long' AND "
                       "entry IS NOT NULL AND stop IS NOT NULL AND entry_ts IS NOT NULL ORDER BY id"):
        f = rth(store, r["symbol"], r["session"])
        if f is None:
            p6.no_bars += 1
            continue
        t0 = pd.Timestamp(r["entry_ts"])
        tg = _dec(xp.long_target, r["entry"], r["stop"])
        pairs = []
        for b in f[(f.ts >= t0) & (f.ts < t0 + LONG_HOLD)].itertuples(index=False):
            d = _dec(xp.decimal_long_target, r["entry"], r["stop"], b.high)
            p6.touch += d is not None and tg is not None and xp.price(b.high) == tg
            pairs.append((b.ts, b.high, xp.binary_long_target(float(r["entry"]), float(r["stop"]), float(b.high)), d))
        p6.compare(f"shadow {r['id']} {r['kind']} {r['symbol']} {r['session']}", pairs)
    if LEDGER.exists():
        rows = {}
        for ln in LEDGER.read_text().splitlines():
            try:
                j = json.loads(ln)
            except ValueError:
                continue
            if isinstance(j, dict) and j.get("book") == "short" and isinstance(j.get("ssr_basis"), dict):
                rows[j.get("id")] = j
        for i, j in sorted(rows.items(), key=lambda kv: str(kv[0])):
            s, pairs = j["ssr_basis"], []
            for what, ref, low in (("today", s.get("prior_close"), s.get("low_before_entry")),
                                   ("carried", s.get("close_before_prior"), s.get("prior_low"))):
                if ref and low:
                    d = _dec(xp.decimal_price_test, ref, low)
                    lv = _dec(xp.level_below, ref)
                    p5.touch += d is not None and lv is not None and xp.price(low) == lv
                    pairs.append((what, low, xp.binary_price_test(float(ref), float(low)), d))
            p5.compare(f"ledger short {i} {j.get('symbol')} {j.get('session')}", pairs)
    print(f"versions: registered {xp.REGISTERED}, corrected {xp.VERSION}; price precision policy: at most {xp.MAX_DP} decimal places")
    for t in (p1, p2, p3, p4, p5, p6):
        print(t.line())
        for d in t.differ:
            print("   DIFFERS:", json.dumps(d))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
