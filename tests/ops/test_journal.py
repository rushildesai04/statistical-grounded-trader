from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sgt.ops import journal
from sgt.ops.store import OpsStore

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = OpsStore(tmp_path / "ops.db")
    with d.tx() as c:
        for i, (sym, st) in enumerate([("OMH", "est_closed"), ("XHLD", "filtered"), ("CKX", "settled")]):
            c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "net, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (sym, "2026-11-09", "new_high", "x", "c", i, st,
                                                                    0.15 if sym == "CKX" else None, "x"))
        c.execute("INSERT INTO scan_trades(symbol, session, bar_ts, detected_at, signal_px, status, weight, created_at) "
                  "VALUES ('SPK','2026-11-09','2026-11-09T15:00:00+00:00','x',5.3,'settled',0.005,'x')")
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, status, created_at, label) "
                  "VALUES ('megacap','NVDA',0.2,'2026-11-06','auto','filled','x','megacap 11-05')")
    return d


def test_entries_cover_every_book_and_skip_filtered(db):
    keys = {e["key"]: e for e in journal.entries(db)}
    assert set(keys) == {"fade:1", "fade:3", "scan:1", "book:1"}
    assert keys["fade:3"]["net"] == 0.15 and keys["book:1"]["book"] == "megacap" and keys["scan:1"]["book"] == "scanner"


def test_save_validates_bounds_strips_and_keeps_history(db):
    assert journal.save(db, "fade:1", "  halted twice\x07 after entry \n", NOW)["text"] == "halted twice after entry"
    assert journal.notes(db)["fade:1"]["text"] == "halted twice after entry"
    for bad in ("fade:99", "fade:0", "fade:1 OR 1=1", "x:1", None, "book:1/../"):
        with pytest.raises(ValueError):
            journal.save(db, bad, "x", NOW)
    with pytest.raises(ValueError):
        journal.save(db, "fade:1", "x" * (journal.MAX_NOTE + 1), NOW)
    with pytest.raises(ValueError):
        journal.save(db, "fade:1", 123, NOW)
    journal.save(db, "fade:1", "", NOW)
    assert journal.notes(db) == {} and journal.notes_since(db, None) == {}
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM trade_notes_history").fetchone()[0] == 2


def test_insights_round_trip(db):
    journal.add_insight(db, "m", "2026-11-09", {"distilled": [{"insight": "i"}], "per_trade": []}, NOW)
    got = journal.insights(db)
    assert got[0]["model"] == "m" and got[0]["body"]["distilled"][0]["insight"] == "i"
