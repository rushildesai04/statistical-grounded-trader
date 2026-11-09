from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_live as fl  # noqa: E402

from sgt.fast import scanner  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402

OPEN = pd.Timestamp("2026-11-09 14:30", tz="UTC")          # 09:30 ET


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def tape(n=30, spike=True, start=OPEN):
    """Flat $5 tape for n bars; the last bar spikes +6% on 10x volume to a new high when ``spike``."""
    rows = [{"ts": start + pd.Timedelta(minutes=i), "open": 5.0, "high": 5.05, "low": 4.95, "close": 5.0,
             "volume": 1000} for i in range(n)]
    if spike:
        rows[-1].update(open=5.1, high=5.35, close=5.3, volume=10_000)
    return pd.DataFrame(rows)


def after(f):
    return (f.ts.iloc[-1] + pd.Timedelta(minutes=1, seconds=5)).to_pydatetime()


def test_detect_the_tested_spike_only_on_complete_bars():
    f = tape()
    ev = scanner.detect(f, after(f))
    assert ev == {"bar_ts": f.ts.iloc[-1], "signal_px": 5.3}
    assert scanner.detect(f, f.ts.iloc[-1].to_pydatetime() + timedelta(seconds=30)) is None    # bar still open
    assert scanner.detect(tape(spike=False), after(tape(spike=False))) is None
    weak = tape()
    weak.loc[weak.index[-1], "volume"] = 3000                                                  # 3x, not 5x
    assert scanner.detect(weak, after(weak)) is None
    early = tape(n=4)                                                                          # < 10 prior bars
    assert scanner.detect(early, after(early)) is None
    pricey = tape()
    pricey[["open", "high", "low", "close"]] *= 20                                             # $100+: outside $1-50
    assert scanner.detect(pricey, after(pricey)) is None


def test_tradable_symbols():
    assert scanner.tradable_symbol("ABCD") and scanner.tradable_symbol("MF")
    assert not scanner.tradable_symbol("CINGW") and not scanner.tradable_symbol("abc") and not scanner.tradable_symbol("A.B")


def test_book_entry_stop_exit_caps_and_first_event(db, monkeypatch):
    f = tape()
    ev = scanner.detect(f, after(f))
    now = after(f)
    assert scanner.record(db, "SPK", ev, now) and not scanner.record(db, "SPK", ev, now)    # first per symbol-day
    start = (ev["bar_ts"] + pd.Timedelta(minutes=1)).to_pydatetime()
    prints = {"t": start - timedelta(seconds=10), "p": 5.3}

    def quote(symbol, since=None):
        return None if since is not None and prints["t"] < since else prints["p"]
    quote.since_aware = True
    sent = []
    scanner.step(db, quote, start + timedelta(seconds=3), sent.append)
    with db.tx() as c:
        assert c.execute("SELECT status FROM scan_trades").fetchone()[0] == "pending"           # stale print: wait
    prints.update(t=start + timedelta(seconds=4), p=5.4)
    scanner.step(db, quote, start + timedelta(seconds=10), sent.append)
    assert len(sent) == 1 and sent[0].startswith("[SGT] 🔎 SCANNER SHORT SPK @ $5.40") and "stop $6.48" in sent[0]
    prints.update(p=6.5)                                                                       # +20.4%: stop
    scanner.step(db, quote, start + timedelta(minutes=3), sent.append)
    assert len(sent) == 2 and sent[1].startswith("[SGT] 🔎 SCANNER OUT SPK · lost -20.4%") and "stop hit" in sent[1]
    with db.tx() as c:
        r = dict(c.execute("SELECT * FROM scan_trades").fetchone())
    assert (r["status"], r["reason"], r["est_entry"], r["est_exit"]) == ("est_closed", "stop", 5.4, 6.5)
    monkeypatch.setattr(scanner, "MAX_OPEN", 0)
    assert scanner.record(db, "CAP", ev, now)
    with db.tx() as c:
        assert c.execute("SELECT status, reason FROM scan_trades WHERE symbol='CAP'").fetchone()[:] == ("skipped", "runaway guard")


def test_official_result_and_void_and_summary(db):
    f = tape(n=100)
    r = {"symbol": "SPK", "bar_ts": f.ts.iloc[29].isoformat()}

    class Cost:
        def round_trip(self, *a):
            return (0.01, "x")
    res = fl.scan_official(r, f, Cost())
    assert res["entry"] == 5.0 and res["cover"] == 5.0 and res["net"] == pytest.approx(-0.01 - fl.LOCATE)
    gap = f[(f.ts <= f.ts.iloc[29]) | (f.ts > f.ts.iloc[29] + pd.Timedelta(minutes=8))]     # halted after the spike
    assert fl.scan_official(r, gap, Cost()) is None
    ev = {"bar_ts": f.ts.iloc[29], "signal_px": 5.0}
    scanner.record(db, "SPK", ev, after(f))
    with db.tx() as c:
        c.execute("UPDATE scan_trades SET status='est_closed'")
    now = datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)
    assert len(scanner.due(db, now)) == 1 and scanner.book(db, 1, "est_closed", res, now)
    line = scanner.summary(db, "2026-11-09")
    assert line.startswith("scanner (experiment, 0.5%/trade): today 1 signals, 1 settled") and "decide at 300" in line


def test_scan_tick_universe_bars_detect_and_step(db):
    f = tape()
    now = after(f)

    def handler(req):
        if req.url.path.endswith("/movers"):
            return httpx.Response(200, json={"gainers": [{"symbol": "SPK", "price": 5.3}, {"symbol": "CINGW", "price": 2},
                                                         {"symbol": "BIG", "price": 120.0}]})
        if req.url.path == "/v2/stocks/bars":
            assert req.url.params["symbols"].split(",")[0] == "SPK" and "NVDA" in req.url.params["symbols"]
            rows = [{"t": t.isoformat(), "o": o, "h": h, "l": lo, "c": c, "v": v}
                    for t, o, h, lo, c, v in f[["ts", "open", "high", "low", "close", "volume"]].itertuples(index=False)]
            return httpx.Response(200, json={"bars": {"SPK": rows}, "next_page_token": None})
        return httpx.Response(404)
    client = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    st = fl.ScanState()
    assert fl.scan_tick(client, db, lambda s, since=None: None, st, now=now) == 1
    assert st.universe[0] == "SPK" and "AAOI" in st.universe and "CINGW" not in st.universe   # + model names
    assert fl.scan_tick(client, db, lambda s, since=None: None, st, now=now) == 0           # first event per day
    sat = datetime(2026, 11, 7, 15, 0, tzinfo=timezone.utc)
    assert fl.scan_tick(client, db, lambda s, since=None: None, fl.ScanState(), now=sat) == 0  # not a session


def test_a_bars_timeout_never_skips_stop_checks(db, monkeypatch):
    f = tape()
    now = after(f)
    ev = scanner.detect(f, now)
    scanner.record(db, "SPK", ev, now)
    with db.tx() as c:
        c.execute("UPDATE scan_trades SET status='open', est_entry=5.3, est_last=5.3, est_entry_at=?", (now.isoformat(),))

    def handler(req):
        if req.url.path.endswith("/movers"):
            return httpx.Response(200, json={"gainers": [{"symbol": "SPK", "price": 5.3}]})
        raise httpx.ReadTimeout("slow")
    client = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    sent = []
    fl.scan_tick(client, db, lambda s, since=None: 6.5, fl.ScanState(), now=now + timedelta(minutes=2), send=sent.append)
    with db.tx() as c:
        assert c.execute("SELECT status, reason FROM scan_trades").fetchone()[:] == ("est_closed", "stop")
    assert sent and "SCANNER OUT SPK" in sent[0]
