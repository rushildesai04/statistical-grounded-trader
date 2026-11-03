"""Live alert-fade book (src/sgt/fast/fade.py, scripts/fade_live.py): gates, estimates, official settlement."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_live as fl  # noqa: E402
import telegram_alert_test as tat  # noqa: E402
from alert_fade_test import LOCATE  # noqa: E402
from sgt.fast import fade  # noqa: E402
from sgt.intraday.calendar import default_calendar  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402
from sgt.ops import approvals as ap  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402

ET = ZoneInfo("America/New_York")
DAY = date(2026, 11, 2)                                      # a Monday session in the past
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


class Outbox(list):
    def __call__(self, text):
        self.append(text)


def alert(sym="ABC", typ="volume_spike", at=T10, mid=1, news=None):
    return fade.LiveAlert(sym, typ, at, "-1", mid, news)


def rows(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM fade_trades ORDER BY id")]


def test_cells_match_the_research_definition():
    assert fade.CELLS == {k for k, v in tat.CELL_OF.items() if v}
    assert (fade.REACTION, fade.MAX_ENTRY_DELAY) == (tat.REACTION.to_pytimedelta(), tat.MAX_ENTRY_DELAY.to_pytimedelta())


def test_unarmed_event_is_skipped_and_blocks_later_alerts_window_filter_does_not(db):
    early = alert(at=datetime(2026, 11, 2, 9, 0, tzinfo=ET).astimezone(timezone.utc))
    assert fade.on_alert(db, early, now=early.posted_at) == "filtered: window"
    assert fade.on_alert(db, alert(), now=T10) == "skipped: not armed"
    ap.arm(db, T10)
    assert fade.on_alert(db, alert(mid=2), now=T10) == "duplicate symbol-session"
    assert fade.on_alert(db, alert("XYZ", "halted"), now=T10) == "not a cell"
    assert fade.on_alert(db, alert("NWS", "news", news=False), now=T10) == "not a cell"
    assert [r["status"] for r in rows(db)] == ["filtered", "skipped"]


def test_arm_is_for_today_only_and_pause_disarms(db):
    ap.arm(db, T10)
    assert ap.armed(db, T10) and not ap.armed(db, T10 + timedelta(days=1))
    ap.pause(db, T10)
    assert not ap.armed(db, T10)
    db.set_flag("paused", "0")
    assert not ap.armed(db, T10)                             # RESUME does not re-arm
    assert ap.parse("arm 123456") == ap.Command("ARM", "123456") and ap.parse("disarm") == ap.Command("DISARM")


def test_live_estimates_entry_stop_and_time_exit(db):
    ap.arm(db, T10)
    out = Outbox()
    fade.on_alert(db, alert("UP"), now=T10)
    fade.on_alert(db, alert("DN", mid=2), now=T10)
    fade.on_alert(db, alert("BIG", mid=3), now=T10)
    px = {"UP": 10.0, "DN": 10.0, "BIG": 75.0}
    fade.monitor(db, out, px.get, now=T10 + timedelta(seconds=20))
    assert [r["status"] for r in rows(db)] == ["open", "open", "filtered"]
    assert out[0].startswith("[SGT] fade SHORT UP ~$10.00") and "stop $12.00" in out[0]
    px.update(UP=12.5, DN=9.0)
    fade.monitor(db, out, px.get, now=T10 + timedelta(minutes=30))
    up, dn = rows(db)[:2]
    assert up["status"] == "est_closed" and up["est_exit"] == 12.5 and up["reason"] == "stop"
    assert dn["status"] == "open" and dn["est_last"] == 9.0
    fade.monitor(db, out, lambda s: None, now=T10 + timedelta(minutes=61))   # no quote: last mark is used
    dn = rows(db)[1]
    assert dn["status"] == "est_closed" and dn["est_exit"] == 9.0 and dn["reason"] == "60 min"
    assert "COVER DN ~$9.00 est (60 min): +10.0%" in out[-1]
    assert fade.day_est_pnl(db, DAY.isoformat()) == pytest.approx(0.05 * (-0.25 + 0.10))


def test_gates_daily_cap_and_loss_stop(db, monkeypatch):
    ap.arm(db, T10)
    monkeypatch.setattr(fade, "MAX_TRADES_DAY", 2)
    for i, s in enumerate(["A", "B", "C"]):
        fade.on_alert(db, alert(s, mid=i), now=T10)
    assert [r["reason"] for r in rows(db)] == ["", "", "daily cap 2"]
    monkeypatch.setattr(fade, "MAX_TRADES_DAY", 15)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET status='est_closed', est_entry=10, est_exit=20, weight=0.05 WHERE symbol='A'")
    assert fade.gate(db, DAY.isoformat(), T10) == "day loss stop -3%"


def test_no_quote_within_five_minutes_skips(db):
    ap.arm(db, T10)
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, Outbox(), lambda s: None, now=T10 + timedelta(minutes=6))
    assert rows(db)[0]["status"] == "skipped"


def test_settle_books_official_results_and_nav(db):
    ap.arm(db, T10)
    for i, s in enumerate(["W", "L", "V", "LATE"]):
        fade.on_alert(db, alert(s, mid=i), now=T10)
    fade.monitor(db, Outbox(), lambda s: 10.0, now=T10 + timedelta(seconds=20))

    def official(due):
        assert set(due.symbol) == {"W", "L", "V", "LATE"}
        ids = dict(zip(due.symbol, due.id))
        return pd.DataFrame([
            {"id": ids["W"], "qualifies": True, "entry_ts": "t", "entry": 10, "cover": 9, "cost_rt": 0.01, "gross": 0.1, "net": 0.085},
            {"id": ids["L"], "qualifies": True, "entry_ts": "t", "entry": 10, "cover": 12, "cost_rt": 0.01, "gross": -0.2, "net": -0.215},
            {"id": ids["V"], "qualifies": False, "why": "outside research filters", "net": None}])  # LATE: no bars yet
    assert fade.settle(db, official, now=T10 + timedelta(minutes=10)) == []        # not due before 80 min
    got = fade.settle(db, official, now=T10 + timedelta(hours=2))
    assert got == ["W +8.50%", "L -21.50%", "V void"]
    assert {r["symbol"]: r["status"] for r in rows(db)} == {"W": "settled", "L": "settled", "V": "void", "LATE": "open"}
    assert fade.nav(db) == pytest.approx(fade.NAV0 * (1 + 0.05 * 0.085) * (1 - 0.05 * 0.215))
    assert fade.digest_lines(db, DAY, T10 + timedelta(hours=2))[0].startswith("fade: $")


def test_listener_heartbeat_warns_in_digest(db):
    fade.monitor(db, Outbox(), lambda s: None, now=T10)
    assert fade.digest_lines(db, DAY, T10 + timedelta(minutes=5)) == []
    assert "listener DOWN" in fade.digest_lines(db, DAY, T10 + timedelta(minutes=20))[0]


def write_bars(store: ParquetBarStore, sym: str, path: dict[int, tuple[float, float]]):
    """``path``: minute-of-RTH -> (open, high); flat 10.00 elsewhere."""
    cal, bars = default_calendar(), []
    t0 = datetime(2026, 11, 2, 9, 30, tzinfo=ET).astimezone(timezone.utc)
    for m in range(390):
        o, h = path.get(m, (10.0, 10.0))
        bars.append(cal.annotate(sym, t0 + timedelta(minutes=m), o, max(o, h), min(o, 10.0), o, 1000))
    store.write_session(sym, DAY, bars)


def test_official_uses_the_research_scorer(tmp_path, monkeypatch):
    store = ParquetBarStore(tmp_path / "bars")
    write_bars(store, "STP", {50: (10.0, 13.0)})              # 10:20 bar spikes through the 20% stop
    write_bars(store, "WIN", {m: (9.0, 9.0) for m in range(32, 120)})  # drifts to 9.00
    monkeypatch.setattr(fl, "BARS", tmp_path / "bars")
    monkeypatch.setattr(fl, "backfill", lambda *a, **k: {})
    due = pd.DataFrame([{"id": 1, "symbol": "STP", "alert_type": "volume_spike", "posted_at": T10.isoformat(),
                         "session": "2026-11-02"},
                        {"id": 2, "symbol": "WIN", "alert_type": "volume_spike", "posted_at": T10.isoformat(),
                         "session": "2026-11-02"},
                        {"id": 3, "symbol": "NOBARS", "alert_type": "volume_spike", "posted_at": T10.isoformat(),
                         "session": "2026-11-02"}])
    res = fl.official(due, now=T10 + timedelta(hours=10)).set_index("id")
    stp, win = res.loc[1], res.loc[2]
    assert stp.qualifies and stp.gross == pytest.approx(-0.20) and stp.cover == pytest.approx(12.0)
    assert win.qualifies and win.gross == pytest.approx(0.10)
    for r in (stp, win):
        assert r.net == pytest.approx(r.gross - r.cost_rt - LOCATE) and r.cost_rt > 0
    assert 3 not in res.index                                 # bars missing, still inside VOID_AFTER: retried
    late = fl.official(due, now=T10 + timedelta(days=4)).set_index("id")
    assert not late.loc[3].qualifies


def test_disarm_before_entry_cancels_pending(db):
    # regression F-1 (review 2026-11-03): DISARM / PAUSE inside the reaction window must not open the trade
    ap.arm(db, T10)
    fade.on_alert(db, alert(), now=T10)
    ap.disarm(db)
    fade.monitor(db, Outbox(), lambda s: 10.0, now=T10 + timedelta(seconds=20))
    r = rows(db)[0]
    assert r["status"] == "skipped" and r["reason"] == "disarmed before entry"


def test_one_failing_row_does_not_stall_the_others(db):
    # regression F-3: a row that raises is logged and skipped; later rows still advance
    ap.arm(db, T10)
    fade.on_alert(db, alert("BAD"), now=T10)
    fade.on_alert(db, alert("OK", mid=2), now=T10)

    def quote(s):
        if s == "BAD":
            raise RuntimeError("boom")
        return 10.0
    fade.monitor(db, Outbox(), quote, now=T10 + timedelta(seconds=20))
    assert [r["status"] for r in rows(db)] == ["pending", "open"]
