"""Fade dollar size setting (owner 2026-11-09): unset = 5% of NAV (unchanged behaviour); set = dollars per trade,
capped at MAX_WEIGHT of the book, gross open short capped at MAX_GROSS; each trade keeps its own weight."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import fade
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def alert(sym, mid):
    return fade.LiveAlert(sym, "volume_spike", T10 - timedelta(seconds=10), "-1", mid, None)


def weights(db):
    with db.tx() as c:
        return [r[0] for r in c.execute("SELECT weight FROM fade_trades ORDER BY id")]


def settled(db, weight, net, sid):
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, weight,"
                  " net, settled_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (f"S{sid}", "2026-10-30", "volume_spike", "2026-10-30T14:00:00+00:00", "-1", sid, "settled", weight,
                   net, f"2026-10-30T16:0{sid}:00+00:00", "2026-10-30T14:00:00+00:00"))


def test_unset_keeps_the_five_percent_default(db):
    assert fade.trade_usd(db) is None and fade.trade_weight(db) == fade.WEIGHT == 0.05
    assert fade.notional(db) == pytest.approx(5_000.0)
    ap.arm(db, T10)
    fade.on_alert(db, alert("AAA", 1), now=T10)
    assert weights(db) == [0.05]


def test_dollar_setting_sizes_new_trades_and_is_capped(db):
    db.set_flag(fade.SIZE_FLAG, "10000")
    ap.arm(db, T10)
    fade.on_alert(db, alert("AAA", 1), now=T10)
    assert weights(db) == [pytest.approx(0.10)] and fade.notional(db) == pytest.approx(10_000.0)
    db.set_flag(fade.SIZE_FLAG, "90000")                     # 90% of the book -> capped at 25%
    assert fade.trade_weight(db) == fade.MAX_WEIGHT == 0.25
    fade.on_alert(db, alert("BBB", 2), now=T10)
    assert weights(db)[-1] == 0.25


def test_dollar_setting_tracks_the_book_value(db):
    settled(db, 0.10, 0.25, 1)                               # +2.5% -> NAV 102,500
    db.set_flag(fade.SIZE_FLAG, "10250")
    assert fade.nav(db) == pytest.approx(102_500.0)
    assert fade.trade_weight(db) == pytest.approx(0.10) and fade.notional(db) == pytest.approx(10_250.0)


@pytest.mark.parametrize("raw", ["abc", "-5", "0", "499", "nan", "inf", " "])
def test_bad_flag_falls_back_to_the_default(db, raw, caplog):
    db.set_flag(fade.SIZE_FLAG, raw)
    assert fade.trade_usd(db) is None and fade.trade_weight(db) == fade.WEIGHT
    if raw.strip():
        assert "invalid" in caplog.text


def test_each_trade_keeps_its_own_weight_in_the_book(db):
    settled(db, 0.05, 0.20, 1)
    settled(db, 0.20, -0.10, 2)
    db.set_flag(fade.SIZE_FLAG, "25000")                     # a later size change never rewrites history
    assert fade.nav(db) == pytest.approx(100_000 * 1.01 * 0.98)


def test_gross_guard_matches_the_old_bound_at_the_default(db, monkeypatch):
    monkeypatch.setattr(fade, "MAX_OPEN", 10_000)            # isolate the gross guard
    ap.arm(db, T10)
    for i in range(39):
        fade.on_alert(db, alert(f"Q{chr(65 + i // 26)}{chr(65 + i % 26)}", i), now=T10)
    assert len(weights(db)) == 39 and fade.caps(db, "2026-11-02") is None          # 39 x 5% + 5% = 200%: allowed
    fade.on_alert(db, alert("LAST", 99), now=T10)
    assert fade.caps(db, "2026-11-02") == "runaway guard: 200% of the book already short"


def test_gross_guard_with_large_trades(db, monkeypatch):
    db.set_flag(fade.SIZE_FLAG, "25000")                     # 25% each: 8 open = 200%
    ap.arm(db, T10)
    for i, s in enumerate(["AA", "BB", "CC", "DD", "EE", "FF", "GG", "HH"]):
        fade.on_alert(db, alert(s, i), now=T10)
    assert len(weights(db)) == 8
    assert fade.on_alert(db, alert("II", 9), now=T10) == "skipped: runaway guard: 200% of the book already short"
    db.set_flag(fade.SIZE_FLAG, "")                          # back to the default: still blocked (200% + 5%)
    assert fade.caps(db, "2026-11-02") is not None


def test_a_drift_alarm_halves_new_fades_only(db):
    """2026-11-12: the live monitor's scale applies to NEW fades; booked weights and the SIZE setting are untouched."""
    from sgt.learning import live_monitor as lm
    ap.arm(db, T10)
    fade.on_alert(db, alert("AAA", 1), now=T10)
    db.set_flag(lm.SCALE_FLAG + fade.BOOK, "0.5")
    fade.on_alert(db, alert("BBB", 2), now=T10)
    assert weights(db) == [pytest.approx(0.05), pytest.approx(0.025)]
    assert fade.notional(db) == pytest.approx(5_000.0) and fade.entry_notional(db) == pytest.approx(2_500.0)
