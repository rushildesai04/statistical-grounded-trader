"""Armed long texts (owner 2026-11-18, option 1): the order is texted once, before it fills; the book is unchanged."""
from __future__ import annotations

import pandas as pd
import pytest

from sgt.fast import est_costs, long_armed as la, long_book as lb, long_shadow as ls
from sgt.fast import patterns as pt
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore

POSTED = pd.Timestamp("2026-11-09 15:00:20", tz="UTC")          # 10:00:20 ET -> start bar 15:01
T1 = pd.Timestamp("2026-11-09 15:01", tz="UTC")
LATEST = ls._latest("2026-11-09")

LEGACY_KINDS = ("L5v2", "L5", "L4b", "L6", "L4a", "L7b", "L7a")


@pytest.fixture(autouse=True)
def one_minute_setups(monkeypatch):
    """These tests drive the book's machinery (fills, exits, texts, settle) through the one-minute setups it traded
    until 2026-11-19. The live default since then (base break) has its own file: tests/fast/test_long_base.py."""
    from sgt.fast import long_book, patterns

    monkeypatch.setattr(long_book, "LIVE_KINDS", LEGACY_KINDS)
    monkeypatch.setattr(long_book, "LIVE_WATCH", patterns.WATCH)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


@pytest.fixture(autouse=True)
def cheap(monkeypatch):
    monkeypatch.setattr(est_costs, "cost", lambda side, sym, at, px: 0.005)      # a 2R target of 1%+ can win


def bars(rows, start=T1):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for i, (o, h, lo, c, v) in enumerate(rows)])


# green 15:01, red candle 15:02 (high 10.55, low 10.20, close 10.30); 15:03 opens 10.30 and breaks 10.55; then a run
DAY = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
            (10.65, 11.6, 10.6, 11.5, 800)] + [(11.5, 11.6, 11.4, 11.5, 100)] * 50)


def alert_row(db, sym="ABCD", posted=POSTED):
    with db.tx() as c:
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                         " reason, weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (sym, "2026-11-09", "volume_spike", posted.isoformat(), "-1", 1, "pending", None, 0.05,
                          posted.isoformat())).lastrowid


def at(minute, second=0):
    return (T1 + pd.Timedelta(minutes=minute, seconds=second)).to_pydatetime()


def feed(day):
    return lambda sym, now: day


def armed(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM long_armed ORDER BY id")]


def trades(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM long_trades ORDER BY id")]


def test_armed_text_goes_out_before_the_fill_once_with_both_orders(db):
    alert_row(db)
    fills, arms = [], []
    assert lb.tick(db, feed(DAY), fills.append, now=at(1, 40), arm=arms.append) == 0 and arms == []   # red not closed
    lb.tick(db, feed(DAY), fills.append, now=at(2, 10), arm=arms.append)      # red candle closed 10 s ago
    assert len(arms) == 1 and fills == [] and trades(db) == []
    t = arms[0]
    assert t.split("\n")[:5] == [                          # owner 2026-11-20: laid out like the fade's cards
        f"[SGT] 🟡 {bold('LONG SETUP ARMED')} · {bold('ABCD')} · candle closed 10:03:00 ET (10s ago)",
        "💬 red-candle break: buy-stop $10.55 · order good until 10:08 ET",
        "🛑 stop $10.20 (-3.3%) · 🎯 target $11.25 (+6.6%)",
        "💬 red-candle close: buy at market now, last $10.30",
        "🛑 stop $10.20 (-1.0%) · 🎯 target $10.50 (+1.9%) from that price"]
    assert "Paper: a LONG text follows if it fills." in t and italic("red-candle break -2.4%/trade") in t
    (a,) = armed(db)
    assert (a["kinds"], a["trigger"], a["stop"], a["ref"]) == ("L4b,L4a", 10.55, 10.2, 10.55) and a["texted_at"] and a["note"] is None
    lb.tick(db, feed(DAY), fills.append, now=at(2, 40), arm=arms.append)      # still pending: no second text
    assert len(arms) == 1
    assert lb.tick(db, feed(DAY), fills.append, now=at(3), arm=arms.append) == 1          # the fill bar closed
    assert len(arms) == 1 and fills[0].startswith(f"[SGT] ✅ {bold('LONG IN')} · {bold('BUY ABCD')} @ {bold('$10.55')}")
    assert len(armed(db)) == 1


def test_the_book_is_the_same_with_and_without_armed_texts(db, tmp_path):
    other = OpsStore(tmp_path / "other.db")
    alert_row(db)
    alert_row(other)
    a_fill, b_fill = [], []
    for k in range(0, 70 * 2):
        lb.tick(db, feed(DAY), a_fill.append, now=at(0, 30 * k), arm=lambda m: None)
        lb.tick(other, feed(DAY), b_fill.append, now=at(0, 30 * k))
    strip = lambda rows: [{k: v for k, v in r.items() if k != "created_at"} for r in rows]  # noqa: E731
    assert strip(trades(db)) == strip(trades(other)) and a_fill == b_fill and len(a_fill) == 2
    assert armed(other) == []


def test_no_armed_text_without_a_sender(db):
    alert_row(db)
    lb.tick(db, feed(DAY), lambda m: None, now=at(2, 10))
    assert armed(db) == []


def test_measure_only_says_nothing_is_traded(db):
    alert_row(db)
    db.set_flag(lb.OFF_FLAG, "1")
    arms = []
    lb.tick(db, feed(DAY), lambda m: None, now=at(2, 10), arm=arms.append)
    assert "Measure-only (your choice): nothing is traded; a 'seen' text follows if it fills." in arms[0]
    assert "Paper:" not in arms[0]


def test_a_market_order_seen_late_is_not_called_but_a_pending_stop_order_still_is(db):
    row = {"id": alert_row(db), "symbol": "ABCD", "session": "2026-11-09", "posted_at": POSTED.isoformat()}
    closed = DAY.iloc[:2]                                   # the red candle is the last closed bar; none has come since
    ready, lines, why = la.group(closed, row, at(2, 89), LATEST)
    assert [s.kind for s, _ in lines] == ["L4b", "L4a"] and why is None
    ready, lines, why = la.group(closed, row, at(2, 91), LATEST)             # a thin name: 91 s and still no bar
    assert [s.kind for s, _ in lines] == ["L4b"]
    assert la.group(closed, row, at(7), LATEST) is None                     # both orders expired


def test_an_order_that_cannot_beat_costs_is_recorded_not_texted(db, monkeypatch):
    monkeypatch.setattr(est_costs, "cost", lambda side, sym, at, px: 0.50)
    alert_row(db)
    arms = []
    lb.tick(db, feed(DAY), lambda m: None, now=at(2, 10), arm=arms.append)
    (a,) = armed(db)
    assert arms == [] and a["texted_at"] is None and a["note"] == "cannot beat costs"
    monkeypatch.setattr(est_costs, "cost", lambda side, sym, at, px: 0.005)
    lb.tick(db, feed(DAY), lambda m: None, now=at(2, 40), arm=arms.append)   # recorded once: never texted later
    assert arms == []


def test_pending_ends_when_the_order_triggers_cancels_or_expires():
    s = pt.Setup("L4b", "long", T1 + 2 * pt.BAR, 10.55, 10.2)
    quiet = bars([(10.3, 10.4, 10.25, 10.35, 100)], start=T1 + 2 * pt.BAR)
    assert la.pending(DAY.iloc[:2], s, at(2, 10), LATEST) and la.pending(pd.concat([DAY.iloc[:2], quiet]), s, at(3, 5), LATEST)
    for o, h, lo in ((10.3, 10.6, 10.3), (10.3, 10.4, 10.2), (10.1, 10.3, 10.1)):          # trigger, cancel, open below
        b = bars([(o, h, lo, 10.3, 100)], start=T1 + 2 * pt.BAR)
        assert not la.pending(pd.concat([DAY.iloc[:2], b]), s, at(3, 5), LATEST)
    assert not la.pending(DAY.iloc[:2], s, at(7), LATEST)                                   # 5 minutes after ready
    assert not la.pending(DAY.iloc[:2], s, at(2, 10), T1 + 2 * pt.BAR)                      # after the last entry minute
    m = pt.Setup("L4a", "long", T1 + 2 * pt.BAR, None, 10.2)
    assert la.pending(DAY.iloc[:2], m, at(2, 10), LATEST) and not la.pending(DAY.iloc[:3], m, at(3, 5), LATEST)


def test_at_most_two_armed_texts_per_alert(db):
    fid = alert_row(db)
    with db.tx() as c:
        for k in range(2):
            c.execute("INSERT INTO long_armed(fade_id, symbol, session, ready, texted_at, created_at) VALUES (?,?,?,?,?,?)",
                      (fid, "ABCD", "2026-11-09", f"2026-11-09T14:5{k}:00+00:00", "x", "x"))
    arms = []
    lb.tick(db, feed(DAY), lambda m: None, now=at(2, 10), arm=arms.append)
    assert arms == [] and armed(db)[-1]["note"] == "cap per alert"


def test_a_failing_armed_text_never_stops_the_book(db):
    alert_row(db)

    def boom(msg):
        raise RuntimeError("sms down")
    fills = []
    lb.tick(db, feed(DAY), fills.append, now=at(2, 10), arm=boom)
    assert lb.tick(db, feed(DAY), fills.append, now=at(3), arm=boom) == 1 and len(fills) == 1


def test_paused_book_sends_no_armed_text(db):
    alert_row(db)
    db.set_flag("paused", "1")
    arms = []
    lb.tick(db, feed(DAY), lambda m: None, now=at(2, 10), arm=arms.append)
    assert arms == [] and armed(db) == []
