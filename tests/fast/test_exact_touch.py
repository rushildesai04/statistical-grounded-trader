"""An exact touch of a short's stop, in the three records that can each call it (GLXG 2026-11-24: the day's high was
the settled replay's stop level to the cent). The records are different things (the settled rule replay on official
one-minute bars, the live paper path on the free feed, the execution model on real quotes: tests/fast/
test_exec_model.py) and none is a broker fill, but they share one rule for the stop: AT or above the level."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import alert_fade_test as aft  # noqa: E402
from sgt.fast import fade, tape_check, would_have  # noqa: E402
from sgt.fast.fade_rule import VETO  # noqa: E402
from sgt.ops import approvals as ap  # noqa: E402
from sgt.ops.store import OpsStore, iso  # noqa: E402

ET = ZoneInfo("America/New_York")
IN = datetime(2026, 11, 24, 12, 58, tzinfo=ET).astimezone(timezone.utc)


def bars(*rows):
    """(minutes after the entry, open, high, close)."""
    return pd.DataFrame([{"ts": pd.Timestamp(IN) + pd.Timedelta(minutes=m), "open": o, "high": h, "low": min(o, c),
                          "close": c} for m, o, h, c in rows])


def test_the_settled_replay_books_a_touch_at_the_level_and_a_gap_at_the_open():
    w = bars((0, 1.0, 1.04, 1.02), (10, 1.125, 1.2, 1.153), (60, 0.95, 0.95, 0.94))
    assert aft.cover(w, 1.0) == pytest.approx(1.2)                        # high == level: a stop, at the level
    assert would_have.cover(w, 1.0, 0.20) == pytest.approx(1.2)
    assert aft.cover(bars((0, 1.0, 1.04, 1.02), (10, 1.125, 1.1999, 1.15), (60, 0.95, 0.95, 0.94)), 1.0) == 0.94
    assert aft.cover(bars((0, 1.0, 1.04, 1.02), (10, 1.31, 1.4, 1.3)), 1.0) == pytest.approx(1.31)   # a gap: the open
    # the entry minute counts (the replay enters at its open; a high later in that minute is after the entry)
    assert aft.cover(bars((0, 1.0, 1.2, 1.1), (30, 1.0, 1.0, 0.9)), 1.0) == pytest.approx(1.2)


def test_the_daytime_official_check_reads_a_touch_the_same_way_but_never_in_the_entry_minute():
    w = bars((0, 1.0, 1.25, 1.02), (10, 1.125, 1.2, 1.153))
    at, px = tape_check.first_cross(w, IN, IN + timedelta(minutes=30), 1.2)
    assert (at, px) == (IN + timedelta(minutes=10), 1.2)                 # minute 0 is skipped: order unknown inside it
    assert tape_check.first_cross(w, IN, IN + timedelta(minutes=30), 1.2001) is None


def test_the_live_paper_path_stops_on_a_print_at_its_own_level_and_not_one_tick_under(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    ap.arm(db, IN)
    with db.tx() as c:
        rid = c.execute(
            "INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, weight,"
            " est_entry, est_entry_at, est_last, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("GLXG", "2026-11-24", "gaining_momentum", iso(IN - timedelta(seconds=20)), "-1", 1, "open", VETO, 0.0125,
             1.02, iso(IN), 1.02, iso(IN))).lastrowid
    sent = []
    fade.monitor(db, sent.append, lambda s, **k: 1.2, now=IN + timedelta(minutes=10))        # the replay's level, not ours
    fade.monitor(db, sent.append, lambda s, **k: 1.2239, now=IN + timedelta(minutes=11))
    with db.read() as c:
        assert c.execute("SELECT status FROM fade_trades WHERE id=?", (rid,)).fetchone()[0] == "open" and sent == []
    fade.monitor(db, sent.append, lambda s, **k: 1.224, now=IN + timedelta(minutes=12))      # 1.02 x 1.2, to the tick
    with db.read() as c:
        r = c.execute("SELECT status, reason, est_exit FROM fade_trades WHERE id=?", (rid,)).fetchone()
    assert (r["status"], r["reason"]) == ("est_closed", "stop") and r["est_exit"] == pytest.approx(1.224)
    assert len(sent) == 1
