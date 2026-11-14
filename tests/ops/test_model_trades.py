"""Daily-book positions as trades (owner 2026-11-14): one trade per position from the close it was entered at to the
close it was exited at, with the book's own dollars."""
from __future__ import annotations

import json

import pytest

from sgt.ops import model_trades as mt
from sgt.ops.book import COST_BP
from sgt.ops.store import OpsStore

C = COST_BP / 1e4


@pytest.fixture
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def order(db, strategy, symbol, target, session, price, status="filled"):
    with db.tx() as c:
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, status, fill_price, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (strategy, symbol, target, session, "auto", status, price, f"{session}T20:50:00+00:00"))


def nav(db, strategy, session, value):
    with db.tx() as c:
        c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                  (strategy, session, value, 0.0, "{}"))


def test_a_one_day_long_and_a_short_are_two_closed_trades_with_book_dollars(db):
    for d, v in (("2026-11-12", 100_000), ("2026-11-13", 99_000)):
        nav(db, "megacap_ext", d, v)
    order(db, "megacap_ext", "AMD", 0.13, "2026-11-12", 585.30)
    order(db, "megacap_ext", "AMD", 0.0, "2026-11-13", 534.06)
    order(db, "megacap_ext", "TSLA", -0.21, "2026-11-12", 397.45)
    order(db, "megacap_ext", "TSLA", 0.0, "2026-11-13", 390.22)
    amd, tsla = mt.episodes(db)
    assert (amd["symbol"], amd["side"], amd["status"], amd["entry_session"], amd["exit_session"]) == \
        ("AMD", "long", "closed", "2026-11-12", "2026-11-13")
    assert amd["key"].startswith("book:") and amd["sessions"] == 1
    assert amd["gross"] == pytest.approx(534.06 / 585.30 - 1) and amd["net"] == pytest.approx(amd["gross"] - 2 * C)
    assert amd["usd"] == pytest.approx(0.13 * 100_000 * (534.06 / 585.30 - 1) - 0.13 * 100_000 * C - 0.13 * 99_000 * C)
    assert tsla["side"] == "short" and tsla["gross"] == pytest.approx(-(390.22 / 397.45 - 1)) and tsla["usd"] > 0


def test_a_resize_keeps_the_trade_open_and_a_flip_closes_and_opens(db):
    for d in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"):
        nav(db, "megacap", d, 100_000)
    order(db, "megacap", "NVDA", 0.20, "2026-11-09", 100.0)
    order(db, "megacap", "NVDA", 0.10, "2026-11-10", 110.0)        # same side, smaller: still one trade
    order(db, "megacap", "NVDA", -0.10, "2026-11-11", 121.0)       # flip: the long ends, a short starts
    got = mt.episodes(db)
    assert [(e["side"], e["status"], e["entry_session"], e["exit_session"]) for e in got] == [
        ("long", "closed", "2026-11-09", "2026-11-11"), ("short", "open", "2026-11-11", None)]
    long = got[0]
    assert long["sessions"] == 2 and long["gross"] == pytest.approx(0.21)
    assert long["usd"] == pytest.approx(0.20 * 100_000 * 0.10 + 0.10 * 100_000 * 0.10 - 0.20 * 100_000 * C - 0.10 * 100_000 * C)
    assert got[1]["net"] is None and got[1]["last_px"] == 121.0 and got[1]["last_w"] == -0.10
    assert got[1]["sessions"] == 1                                   # marked once after its entry (11-12)


def test_scheduled_orders_and_zero_targets(db):
    order(db, "megacap_long", "JPM", 0.0, "2026-11-13", 346.0)                       # a zero target with no position: nothing
    order(db, "megacap_long", "AAPL", 0.25, "2026-11-16", None, status="scheduled")   # ordered, fills at the close
    order(db, "megacap_long", "MSFT", 0.2, "2026-11-13", 523.0)
    order(db, "megacap_long", "MSFT", 0.0, "2026-11-16", None, status="scheduled")    # exit ordered, not filled yet
    order(db, "megacap_long", "VOID", 0.2, "2026-11-13", 10.0, status="void")
    got = {e["symbol"]: e for e in mt.episodes(db)}
    assert set(got) == {"AAPL", "MSFT"}
    assert got["AAPL"]["status"] == "scheduled" and got["AAPL"]["entry"] is None and got["AAPL"]["usd"] is None
    assert got["MSFT"]["status"] == "open" and got["MSFT"]["exit_scheduled"] == "2026-11-16"


def test_forecast_and_tested_record_are_read_defensively(tmp_path):
    d = tmp_path / "megacap_ext" / "decisions"
    d.mkdir(parents=True)
    (d / "2026-11-12.json").write_text(json.dumps({"positions": {"AMD": {"pred": 0.0125, "q_lo": -0.0065, "q_hi": 0.0118,
                                                                       "position": 1}, "BAD": {"pred": "x"}}}))
    (d / "2026-11-13.json").write_text("{not json")
    assert mt.forecast(tmp_path, "megacap_ext", "AMD", "2026-11-12") == {"pred": 0.0125, "q_lo": -0.0065, "q_hi": 0.0118}
    assert mt.forecast(tmp_path, "megacap_long", "AMD", "2026-11-12") == {"pred": 0.0125, "q_lo": -0.0065, "q_hi": 0.0118}
    for args in (("megacap_ext", "BAD", "2026-11-12"), ("megacap_ext", "AMD", "2026-11-13"),
                 ("megacap_ext", "AMD", "2026-11-14"), ("etf:B_6040", "AMD", "2026-11-12")):
        assert mt.forecast(tmp_path, *args) is None
    stats = {"AMD": {"long": {"n": 188, "win": 0.51, "avg": 0.006}, "short": {"n": "x"}}, "X": 3}
    assert mt.tested(stats, "AMD", "long") == {"n": 188, "win": 0.51, "avg": 0.006}
    assert mt.tested(stats, "AMD", "short") is None and mt.tested(stats, "X", "long") is None
    assert mt.tested(None, "AMD", "long") is None and mt.tested(stats, "ZZZ", "long") is None


def _bars(start, n, step, p0=100.0):
    import pandas as pd

    ts = pd.date_range(start, periods=n, freq=step, tz="UTC")
    px = [p0 + i * 0.5 for i in range(n)]
    return pd.DataFrame({"ts": ts, "open": px, "high": [p + 0.3 for p in px], "low": [p - 0.3 for p in px],
                         "close": [p + 0.1 for p in px], "volume": [1000.0] * n})


def test_book_view_closed_trade_uses_the_books_fills_and_marks_entry_and_exit(db):
    from sgt.ops import charts

    for d in ("2026-11-12", "2026-11-13"):
        nav(db, "megacap_ext", d, 100_000)
    order(db, "megacap_ext", "AMD", 0.13, "2026-11-12", 585.30)
    order(db, "megacap_ext", "AMD", 0.0, "2026-11-13", 534.06)
    e = mt.episodes(db)[0]
    bars = _bars("2026-11-12T14:30:00Z", 156, "5min")                      # two sessions' worth of 5-minute bars
    v = charts.book_view(e, bars, decided="2026-11-12T20:50:00+00:00", entered="2026-11-12T20:59:00+00:00",
                         exited="2026-11-13T03:25:00+00:00", timeframe="5Min", book="megacap 15",
                         forecast={"pred": 0.0125}, tested={"n": 188, "win": 0.51, "avg": 0.006})
    assert (v["kind"], v["book"], v["key"], v["side"], v["symbol"]) == ("model", "megacap 15", e["key"], "long", "AMD")
    assert v["official"] and v["ret"] == pytest.approx(e["net"]) and v["usd"] == pytest.approx(e["usd"])
    assert (v["entry"], v["exit"]) == (585.30, 534.06) and v["stop"] is None and v["target"] is None
    assert v["conf"] is None and v["signals"] is None and v["forecast"] == {"pred": 0.0125} and v["tested"]["n"] == 188
    assert [m["kind"] for m in v["markers"]] == ["alert", "buy", "sell"] and len(v["candles"]) > 50
    assert v["sessions"] == 1 and v["weight"] == 0.13 and v["timeframe"] == "5Min"
    json.dumps(v, allow_nan=False)                                          # page-safe


def test_book_view_open_short_is_marked_to_the_last_bar_as_an_estimate(db):
    from sgt.ops import charts

    nav(db, "megacap_ext", "2026-11-12", 100_000)
    order(db, "megacap_ext", "TSLA", -0.2, "2026-11-12", 400.0)
    e = mt.episodes(db)[0]
    bars = _bars("2026-11-12T14:30:00Z", 100, "5min", p0=350.0)             # last close 399.6
    v = charts.book_view(e, bars, decided="2026-11-12T20:50:00+00:00", entered="2026-11-12T20:59:00+00:00",
                         exited=None, timeframe="5Min", book="megacap 15")
    last = float(bars.close.iloc[-1])
    assert not v["official"] and v["status"] == "open" and v["exit"] == pytest.approx(last)
    assert v["ret"] == pytest.approx(-(last / 400.0 - 1))
    assert v["usd"] == pytest.approx(-0.2 * 100_000 * (last / 400.0 - 1) - 0.2 * 100_000 * C)
    assert [m["kind"] for m in v["markers"]] == ["alert", "short"]


def test_book_views_builds_cards_from_the_db_and_survives_a_symbol_with_no_bars(db, tmp_path, monkeypatch):
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import trade_charts as tc

    for d in ("2026-11-12", "2026-11-13"):
        nav(db, "megacap_ext", d, 100_000)
    order(db, "megacap_ext", "AMD", 0.13, "2026-11-12", 585.30)
    order(db, "megacap_ext", "AMD", 0.0, "2026-11-13", 534.06)
    order(db, "megacap_ext", "NOBARS", 0.1, "2026-11-12", 10.0)
    order(db, "megacap_ext", "NOBARS", 0.0, "2026-11-13", 11.0)
    order(db, "megacap_long", "JPM", 0.25, "2026-11-16", None, status="scheduled")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.url.params["timeframe"]))
        if "NOBARS" in req.url.path:
            return httpx.Response(200, json={"bars": None})
        b = _bars("2026-11-12T14:30:00Z", 156, "5min")
        return httpx.Response(200, json={"bars": [{"t": t.isoformat(), "o": o, "h": h, "l": lo, "c": c, "v": v} for
                                                  t, o, h, lo, c, v in b.itertuples(index=False)]})

    monkeypatch.setattr(tc, "SPAN_CACHE", tmp_path / "spans")
    now = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)                # Saturday
    with httpx.Client(base_url="https://data.example", transport=httpx.MockTransport(handler)) as client:
        views = tc.book_views(db, client, now, reports=tmp_path)
    assert [v["symbol"] for v in views] == ["AMD"] and views[0]["book"] == "megacap 15" and views[0]["official"]
    assert ("/v2/stocks/AMD/bars", "5Min") in seen and views[0]["tested"] is not None
    assert (tmp_path / "spans" / "5Min").exists()                           # a finished span is cached
