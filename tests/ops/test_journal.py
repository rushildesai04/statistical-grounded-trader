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


def _long(db, status, kind="L4a", net=None, shadow=None):
    """A live long-book trade on a filtered alert (longs are considered on every research alert)."""
    with db.tx() as c:
        fid = c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                        "created_at) VALUES ('KULR','2026-11-10','volume_spike','2026-11-10T14:41:00+00:00','c',?,"
                        "'filtered','x')", (100 + c.execute("SELECT COUNT(*) FROM fade_trades").fetchone()[0],)).lastrowid
        lid = c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, "
                        "entry_ts, est_entry, est_exit, est_exit_at, reason, net, created_at) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, "KULR", "2026-11-10", kind, status, 4.31, 4.20, 4.53, 0.05, "2026-11-10T14:44:00+00:00",
                         4.32, 4.27, "2026-11-10T14:52:00+00:00", "stop", net, "x")).lastrowid
        if shadow:
            c.execute("INSERT INTO long_shadow(fade_id, symbol, session, kind, side, entry, exit, net, created_at) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (fid, "KULR", "2026-11-10", kind, "long", *shadow, net, "x"))
    return lid


def test_long_book_trades_are_journal_entries_with_official_prices_only_once_settled(db):
    """2026-11-10: the long book was missing from the journal, so the AI reviews, the nightly brain and the Notes tab
    never saw a long. 'none' rows (no setup) are not trades; the shadow's prices count only once settled."""
    est = _long(db, "est_closed", shadow=(4.33, 4.19))
    settled = _long(db, "settled", kind="L5v2", net=-0.031, shadow=(4.33, 4.19))
    _long(db, "none")
    keys = {e["key"]: e for e in journal.entries(db)}
    assert {k for k in keys if k.startswith("long:")} == {f"long:{est}", f"long:{settled}"}
    e = keys[f"long:{est}"]
    assert (e["book"], e["side"], e["kind"], e["entry"], e["cover"]) == ("long book", "long", "L4a", None, None)
    assert e["posted_at"] == "2026-11-10T14:41:00+00:00" and e["est_entry_at"] == "2026-11-10T14:44:00+00:00"
    s = keys[f"long:{settled}"]
    assert (s["entry"], s["cover"], s["net"], s["trigger"], s["target"]) == (4.33, 4.19, -0.031, 4.31, 4.53)


def test_notes_save_on_a_long_trade_and_reject_unknown_long_ids(db):
    lid = _long(db, "est_closed")
    assert journal.save(db, f"long:{lid}", "bought the first red candle into a halt", NOW)["key"] == f"long:{lid}"
    for bad in ("long:999", "long:0", "longs:1", "long:1;DROP"):
        with pytest.raises(ValueError):
            journal.save(db, bad, "x", NOW)
