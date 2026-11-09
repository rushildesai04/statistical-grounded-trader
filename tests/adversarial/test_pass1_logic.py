"""PASS 1 adversarial reproducers (LOGIC/CORRECTNESS + EDGE CASES) for the scanner book.

These assert PRODUCTION behavior and are written to FAIL on current code where a real bug exists.
Scope: src/sgt/fast/scanner.py and scripts/fade_live.py (scanner driver) vs src/sgt/fast/fade.py close math.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

from sgt.fast import fade, scanner
from sgt.fast.fade import ET
from sgt.intraday.calendar import default_calendar
from sgt.ops.store import OpsStore, iso
import fade_live

UTC = timezone.utc


def _store(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def _insert_open(db, session, entry_et, px=10.0):
    bar_ts = entry_et - timedelta(minutes=1)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO scan_trades(symbol, session, bar_ts, detected_at, signal_px, status, weight, "
            "est_entry, est_entry_at, est_last, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("ABCD", session, iso(bar_ts), iso(bar_ts), px, "open", 0.005, px, iso(entry_et), px, iso(bar_ts)),
        )
        return cur.lastrowid


def _status(db, rid):
    with db.tx() as c:
        return c.execute("SELECT status FROM scan_trades WHERE id=?", (rid,)).fetchone()[0]


def _quote(sym, since=None):
    return None


def test_scan_close_exit_never_driven_after_1600(tmp_path):
    db = _store(tmp_path)
    entry = datetime(2026, 11, 10, 15, 30, tzinfo=ET)
    rid = _insert_open(db, "2026-11-10", entry)
    now = datetime(2026, 11, 10, 16, 0, 30, tzinfo=ET).astimezone(UTC)

    state = SimpleNamespace(universe=[], at=now.astimezone(ET))
    sent = []
    fade_live.scan_tick(None, db, _quote, state, now, sent.append)
    via_driver = _status(db, rid)

    scanner.step(db, _quote, now, sent.append)
    via_step = _status(db, rid)

    assert via_driver == "est_closed", (
        "scan_tick left the trade %r at 16:00:30; step() closes it to %r. "
        "The live close-exit (and its OUT text) is never driven at/after 16:00." % (via_driver, via_step)
    )


def test_scanner_ignores_early_close(tmp_path):
    session = "2026-11-27"
    real_close = default_calendar().bounds(date.fromisoformat(session)).rth_close
    assert real_close.astimezone(ET).time() == time(13, 0)

    started = datetime(2026, 11, 27, 12, 50, tzinfo=ET)
    end, _ = fade._exit_at(session, started.astimezone(UTC))
    assert end.astimezone(ET).time() == time(13, 0)

    db = _store(tmp_path)
    rid = _insert_open(db, session, started)
    now = datetime(2026, 11, 27, 13, 5, tzinfo=ET).astimezone(UTC)
    scanner.step(db, _quote, now, None)

    assert _status(db, rid) == "est_closed", (
        "scanner.step left the trade open 5 min after the real 13:00 ET early close; it uses a hardcoded 16:00 "
        "boundary (out_at=13:50) while the fade and the exchange closed at 13:00."
    )
