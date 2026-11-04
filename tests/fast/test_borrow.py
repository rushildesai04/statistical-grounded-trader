"""Borrow reality layer (src/sgt/fast/borrow.py) and its use in the fade book (plan 2026-11-04 W1)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from sgt.fast import borrow, fade
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)
HEAD = "#BOF|2026.11.02|09:45:13\n#SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|FIGI|\n"


def payload() -> bytes:
    rows = ["ABC|USD|ABC CORP|1|X|-40.1|45.0|12000|F|", "BRK B|USD|BERKSHIRE|2|X|3.6|0.25|>10000000|F|",
            "EUR1|EUR|EURO|3|X|1|1|5|F|", "049323AB4|USD|BOND|4|X|3.6|0.25|2800000|F|", "BAD|USD|BAD|5|X|x|y|z|F|"]
    return (HEAD + "\n".join(rows) + "\n#EOF\n").encode()


def test_parse_keeps_us_symbols_lower_bounds_and_file_time():
    df, stamp = borrow.parse_ibkr(payload())
    got = df.set_index("symbol")
    assert set(got.index) == {"ABC", "BRK.B"}                   # non-USD, bond CUSIP, malformed dropped
    assert got.loc["ABC"].fee == 45.0 and got.loc["ABC"].avail == 12000 and got.loc["BRK.B"].avail == 10_000_000
    assert stamp == datetime(2026, 11, 2, 9, 45, 13, tzinfo=ET).astimezone(timezone.utc)


def test_snapshot_refuses_a_truncated_file_and_lookup_rules(tmp_path):
    with pytest.raises(ValueError):
        borrow.snapshot(T10, tmp_path, payload())
    calls = []

    def asset(sym):
        calls.append(sym)
        if sym == "ERR":
            raise RuntimeError("down")
        return {"easy_to_borrow": sym == "ABC", "shortable": True}
    df, _ = borrow.parse_ibkr(payload())
    filler = pd.DataFrame({"symbol": [f"Z{i}" for i in range(1200)], "fee": 0.3, "rebate": 1.0, "avail": 100})
    big = pd.concat([df, filler]).assign(snap=T10.isoformat())
    big.to_parquet(tmp_path / "latest.parquet")
    book = borrow.BorrowBook(tmp_path, alpaca_asset=asset)
    assert book.lookup("ABC", T10) == {"ib_avail": 12000, "ib_fee": 45.0, "ib_snap": T10.isoformat(),
                                       "alp_etb": 1, "alp_shortable": 1}
    assert book.lookup("NOPE", T10)["ib_avail"] == 0                       # not lendable at IBKR
    assert book.lookup("ABC", T10 + timedelta(hours=4))["ib_snap"] is None  # stale snapshot not used
    book.lookup("ABC", T10)
    assert calls.count("ABC") == 1                                          # Alpaca flags cached per day
    assert book.lookup("ERR", T10)["alp_etb"] is None


def test_fade_stamps_borrow_once_and_shows_it_in_the_approval_text(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    out = []
    facts = {"ib_avail": 12000, "ib_fee": 45.0, "ib_snap": T10.isoformat(), "alp_etb": 0, "alp_shortable": 1}
    seen = []

    def lookup(sym, now):
        seen.append(sym)
        return facts
    fade.on_alert(db, fade.LiveAlert("ABC", "volume_spike", T10, "-1", 1), now=T10)
    fade.monitor(db, out.append, lambda s: 10.0, now=T10 + timedelta(seconds=15), borrow=lookup)
    fade.monitor(db, out.append, lambda s: 10.0, now=T10 + timedelta(seconds=25), borrow=lookup)
    assert seen == ["ABC"]
    assert "Borrow: IBKR 12,000 sh @ 45.0%/yr." in out[0]
    with db.tx() as c:
        r = dict(c.execute("SELECT * FROM fade_trades").fetchone())
    assert (r["ib_avail"], r["ib_fee"], r["alp_etb"]) == (12000, 45.0, 0)
    assert fade.borrow_note({"ib_avail": 0}) == "IBKR: none to borrow"


def test_store_adds_new_columns_to_an_existing_database(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as c:                     # the pre-2026-11-04 table on the runner's volume
        c.execute("CREATE TABLE fade_trades(id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, "
                  "session TEXT NOT NULL, alert_type TEXT NOT NULL, posted_at TEXT NOT NULL, channel TEXT NOT NULL, "
                  "message_id INTEGER NOT NULL, status TEXT NOT NULL, reason TEXT, weight REAL, est_entry REAL, "
                  "est_entry_at TEXT, est_last REAL, est_exit REAL, est_exit_at TEXT, entry_ts TEXT, entry REAL, "
                  "cover REAL, cost_rt REAL, gross REAL, net REAL, settled_at TEXT, edited INTEGER NOT NULL DEFAULT 0, "
                  "created_at TEXT NOT NULL)")
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "created_at) VALUES ('OLD','2026-11-02','news','t','-1',1,'settled','t')")
    OpsStore(path)
    OpsStore(path)                                        # idempotent
    with sqlite3.connect(path) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(fade_trades)")}
        assert {"ib_avail", "ib_fee", "ib_snap", "alp_etb", "alp_shortable"} <= cols
        assert c.execute("SELECT symbol FROM fade_trades").fetchone() == ("OLD",)
