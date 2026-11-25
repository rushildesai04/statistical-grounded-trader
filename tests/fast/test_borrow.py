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
            "EUR1|EUR|EURO|3|X|1|1|5|F|", "NAF|USD|NA FEE|6|X|NA|NA|900|F|", "049323AB4|USD|BOND|4|X|3.6|0.25|2800000|F|", "BAD|USD|BAD|5|X|x|y|z|F|"]
    return (HEAD + "\n".join(rows) + "\n#EOF\n").encode()


def test_parse_keeps_us_symbols_lower_bounds_and_file_time():
    df, stamp = borrow.parse_ibkr(payload())
    got = df.set_index("symbol")
    assert set(got.index) == {"ABC", "BRK.B", "NAF"}            # non-USD, bond CUSIP, malformed dropped
    assert got.loc["NAF"].avail == 900 and pd.isna(got.loc["NAF"].fee)
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
    assert book.lookup("NAF", T10 - timedelta(minutes=1))["ib_fee"] is None  # lendable, fee unpublished
    assert fade.borrow_note({"ib_avail": 900, "ib_fee": None}) == "IBKR 900 sh fee n/a"
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
    fade.on_alert(db, fade.LiveAlert("ABC", "volume_spike", T10 - timedelta(seconds=10), "-1", 1), now=T10)  # +10 s = bar start
    fade.monitor(db, out.append, lambda s: 10.0, now=T10 + timedelta(seconds=15), borrow=lookup)
    fade.monitor(db, out.append, lambda s: 10.0, now=T10 + timedelta(seconds=25), borrow=lookup)
    assert seen == ["ABC"]
    assert "🏦 IBKR 12k sh @ 45%/yr" in out[0]
    with db.tx() as c:
        r = dict(c.execute("SELECT * FROM fade_trades").fetchone())
    assert (r["ib_avail"], r["ib_fee"], r["alp_etb"]) == (12000, 45.0, 0)
    assert fade.borrow_note({"ib_avail": 0}) == "none at IBKR (real short unlikely)"


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


# --- owner 2026-11-16 ("pretend like its a real scenario"): no shares at IBKR, no paper short ---------------------------

def _run(tmp_path, facts, px=10.0, sym="ABC"):
    """One alert through the book with a borrow source; returns (db, texts, the row)."""
    db = OpsStore(tmp_path / "ops.db")
    out = []
    fade.on_alert(db, fade.LiveAlert(sym, "volume_spike", T10 - timedelta(seconds=10), "-1", 1), now=T10)
    fade.monitor(db, out.append, lambda s: px, now=T10 + timedelta(seconds=15), borrow=lambda s, now: facts)
    with db.tx() as c:
        return db, out, dict(c.execute("SELECT * FROM fade_trades").fetchone())


def _facts(avail):
    return {"ib_avail": avail, "ib_fee": 45.0, "ib_snap": T10.isoformat(), "alp_etb": 0, "alp_shortable": 0}


def test_no_shares_at_ibkr_means_no_paper_short_and_no_text(tmp_path):
    db, out, r = _run(tmp_path, _facts(0))
    assert (r["status"], r["reason"]) == ("filtered", fade.BORROW_NONE)
    assert r["est_entry"] is None and out == []                       # never opened, nothing texted
    assert fade.day_est_pnl(db, "2026-11-02") == 0.0


def test_unknown_borrow_is_no_trade(tmp_path):
    """Fail closed: no fresh IBKR list (every fact None) is not permission to short."""
    none = {"ib_avail": None, "ib_fee": None, "ib_snap": None, "alp_etb": None, "alp_shortable": None}
    _, out, r = _run(tmp_path, none)
    assert (r["status"], r["reason"]) == ("filtered", fade.BORROW_UNKNOWN) and out == []
    _, out, r = _run(tmp_path / "b", {**none, "alp_etb": 1, "alp_shortable": 1})   # Alpaca alone does not decide
    assert (r["status"], r["reason"]) == ("filtered", fade.BORROW_UNKNOWN) and out == []


def test_the_trade_needs_its_own_share_count_not_just_some_shares(tmp_path):
    """$5,000 at $10.00 is 500 shares: 499 lendable is too few, 500 is enough."""
    _, out, r = _run(tmp_path, _facts(499))
    assert (r["status"], r["reason"]) == ("filtered", fade.BORROW_FEW) and out == []
    _, out, r = _run(tmp_path / "b", _facts(500))
    assert r["status"] == "open" and r["est_entry"] == 10.0 and len(out) == 1
    _, out, r = _run(tmp_path / "c", _facts(500), px=9.99)            # a lower price needs one share more: 501
    assert (r["status"], r["reason"]) == ("filtered", fade.BORROW_FEW)
    db = OpsStore(tmp_path / "d" / "ops.db")
    assert fade.no_shares(db, {"ib_avail": 250, "weight": 0.025}, 10.0) is None      # half size (drift guard): 250
    assert fade.no_shares(db, {"ib_avail": 249, "weight": 0.025}, 10.0) == fade.BORROW_FEW
    assert fade.no_shares(db, {"ib_avail": -1, "weight": 0.05}, 10.0) == fade.BORROW_NONE
    assert fade.no_shares(db, {"weight": 0.05}, 10.0) == fade.BORROW_UNKNOWN


def test_a_short_not_taken_for_borrow_is_still_an_event_for_f8_the_longs_and_the_digest(tmp_path):
    from datetime import date

    from sgt.fast import long_shadow
    from sgt.ops import digest

    db, _, r = _run(tmp_path, _facts(0))
    assert fade.prior_alert(db, "ABC", date(2026, 11, 3)) == "2026-11-02"          # F8 counts it as an alert
    assert [x["symbol"] for x in long_shadow.due(db, "2026-11-01")] == ["ABC"]      # a long needs no borrowed shares
    head, usd = digest.fade_today(db, date(2026, 11, 2))
    assert usd == 0.0 and len(head) == 1
    assert "1 alert not shorted on IBKR's lendable list at the alert (no shares listed 1; " in head[0]
    from sgt.ops.sms_style import italic
    assert italic("that broker at that time, not every broker") in head[0] and "alerts" not in head[0]
    assert "could not" not in head[0]                 # owner 2026-11-24: one broker's list is not every broker's
    with db.tx() as c:                                # the three reasons are counted as what they are
        for i, why in enumerate((fade.BORROW_FEW, fade.BORROW_UNKNOWN, fade.BORROW_UNKNOWN)):
            c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (f"Q{i}", "2026-11-02", "new_high",
                      "2026-11-02T15:00:00+00:00", "-1", 900 + i, "filtered", why, "2026-11-02T15:00:01+00:00"))
    head, _ = digest.fade_today(db, date(2026, 11, 2))
    assert "4 alerts not shorted" in head[0]
    assert "(no shares listed 1, too few for the size 1, no fresh list 2; " in head[0]
    assert set(fade.BORROW_REASONS) <= set(fade.EVENT_FILTERS) and set(fade.BORROW_REASONS) <= set(long_shadow.SHORT_SKIPS)


def test_an_approved_or_auto_entered_short_still_needs_shares(tmp_path, monkeypatch):
    """Borrow is not a preference: the owner's OK cannot create shares (the approval path skips the news and beta
    gates, never this one), and neither can silence at the deadline."""
    import re

    from sgt.ops import approvals as ap
    from sgt.ops.sms_style import bold

    monkeypatch.setattr(fade, "ASK_FIRST", True)
    db = OpsStore(tmp_path / "ops.db")
    out = []
    lookup = lambda s, now: _facts(0)  # noqa: E731
    for mid, sym in enumerate(("YES", "AUTO"), 1):
        fade.on_alert(db, fade.LiveAlert(sym, "volume_spike", T10 - timedelta(seconds=10), "-1", mid), now=T10)
    fade.monitor(db, out.append, lambda s: 10.0, now=T10 + timedelta(seconds=15), borrow=lookup)   # two requests
    assert len(out) == 2
    yes = next(t for t in out if bold("YES") in t)
    _, row = ap.answer(db, ap.Command("OK", re.search(r"OK (\d{6}|\d{2})\b", yes).group(1)), "SM1",
                       now=T10 + timedelta(minutes=2))
    assert row["status"] == "approved"
    fade.monitor(db, out.append, lambda s: 10.0, now=T10 + timedelta(minutes=2, seconds=10), borrow=lookup)
    fade.monitor(db, out.append, lambda s: 10.0, now=T10 + timedelta(minutes=4, seconds=45), borrow=lookup)   # deadline
    with db.tx() as c:
        got = {r["symbol"]: (r["status"], r["reason"], r["est_entry"]) for r in c.execute("SELECT * FROM fade_trades")}
    assert got == {"YES": ("filtered", fade.BORROW_NONE, None), "AUTO": ("filtered", fade.BORROW_NONE, None)}
    assert len(out) == 2                                                  # no FADE IN text for either


def test_without_a_borrow_source_the_rule_is_not_applied_and_the_listener_always_has_one():
    """The rule runs wherever a borrow source is passed (as the news and beta checks do). The listener must pass one:
    without it the book would short blind again."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "fade_live.py").read_text()
    assert "fade.monitor(db, send, quote, None, book.lookup," in src


def test_the_nightly_says_how_many_alerts_were_recorded_under_the_entry_rule_and_on_what_evidence(tmp_path):
    """Owner 2026-11-25: a substitute short entry is record-only. The nightly counts them and never calls one a halt."""
    from datetime import date

    from sgt.fast import exec_check
    from sgt.ops import digest
    from sgt.ops.sms_style import italic
    from sgt.ops.store import OpsStore

    db = OpsStore(tmp_path / "ops.db")
    rows = [("AAA", fade.ELIG_SUBSTITUTE, exec_check.OFF_SUBSTITUTE), ("BBB", fade.ELIG_UNKNOWN, None),
            ("CCC", fade.ELIG_UNKNOWN, exec_check.OFF_DELAYED.format(at="10:03"))]
    with db.tx() as c:
        for i, (sym, elig, off) in enumerate(rows):
            c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                      "entry_elig, entry_official, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (sym, "2026-11-02", "new_high", "2026-11-02T15:00:00+00:00", "-1", i, "skipped",
                       fade.SUBSTITUTE_REASON, elig, off, "2026-11-02T15:00:01+00:00"))
    head, usd = digest.fade_today(db, date(2026, 11, 2))
    assert usd == 0.0 and len(head) == 1
    assert ("3 alerts recorded, not shorted: the registered entry minute passed before our free feed showed a price "
            "(earlier trading confirmed 1, not knowable in time 2; official bars since: 1 had a genuinely later first "
            "trade; " + italic("no replacement entry") + ")") in head[0]
    assert "halt" not in head[0]

