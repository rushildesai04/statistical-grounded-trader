"""The revision block of the nightly report (owner 2026-11-24): a texted live estimate against the settled result.
Two records of one trade, neither a broker fill; the execution model is never part of it."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import fade, text_order
from sgt.ops import digest, revisions as rv, summary
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
DAY = date(2026, 11, 24)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def short(db, symbol="GLXG", session="2026-11-24", status="settled", **cols) -> int:
    """GLXG 2026-11-24 as it stands on the runner, unless ``cols`` say otherwise."""
    row = {"symbol": symbol, "session": session, "alert_type": "gaining_momentum", "posted_at": f"{session}T17:57:40+00:00",
           "channel": "-1", "message_id": 1, "status": status, "reason": "60 min", "weight": 0.0125,
           "est_entry": 1.02, "est_entry_at": f"{session}T18:00:32.542634+00:00", "est_last": 0.9598, "est_exit": 0.9598,
           "est_exit_at": f"{session}T19:00:37.816132+00:00", "entry_ts": f"{session} 17:58:00+00:00", "entry": 1.0,
           "cover": 1.2, "cost_rt": 0.0073386, "gross": -0.2, "net": -0.2123386,
           "settled_at": "2026-11-25T01:40:01.567579+00:00", "created_at": f"{session}T17:57:42+00:00", **cols}
    if status != "settled":
        row.update({k: None for k in ("entry_ts", "entry", "cover", "cost_rt", "gross", "net")} | {
            "settled_at": cols.get("settled_at")} | {k: v for k, v in cols.items()})
    with db.tx() as c:
        return c.execute(f"INSERT INTO fade_trades({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",  # noqa: S608
                         tuple(row.values())).lastrowid


def long(db, fade_id, symbol="BENF", session="2026-11-24", status="settled", **cols) -> int:
    row = {"fade_id": fade_id, "symbol": symbol, "session": session, "kind": "L8", "status": status, "stop": 0.8949,
           "target": 1.0002, "weight": 0.0036, "entry_ts": f"{session}T19:04:00+00:00", "est_entry": 0.93,
           "est_exit": 0.9775, "est_exit_at": f"{session}T19:33:00+00:00", "reason": "time", "net": 0.0417,
           "created_at": f"{session}T19:04:00.9+00:00", "entry": 0.9299, "fill_ts": f"{session}T19:04:00+00:00",
           "exit": 0.9774, "exit_ts": f"{session}T19:33:00+00:00", "exit_reason": "time",
           "settled_at": "2026-11-25T01:40:06+00:00", **cols}
    with db.tx() as c:
        return c.execute(f"INSERT INTO long_trades({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",  # noqa: S608
                         tuple(row.values())).lastrowid


def test_the_rule_is_five_points_or_a_change_of_sign():
    assert rv.large(0.047, -0.212) and rv.large(-0.03, -0.08) and rv.large(0.10, 0.05)
    assert rv.large(0.001, -0.001)                                   # the sign alone
    assert not rv.large(0.206, 0.246)                                # JZ 11-24: 4 points, same sign
    assert not rv.large(0.0, -0.01) and not rv.large(None, -0.2) and not rv.large(0.1, None)
    # 2026-11-25: exactly 5.0 points is listed and a hair under is not, with no tolerance (in doubles the first
    # difference below is 0.04999999999999999)
    assert abs(0.25 - 0.30) < 0.05 and rv.large(0.30, 0.25) and rv.large(0.10, 0.05) and rv.large(0.047, -0.003)
    assert not rv.large(0.10, 0.050001) and not rv.large(0.047, 0.0969)


def test_glxg_reads_as_two_records_with_both_entries_stops_exits_and_the_cause(db):
    rid = short(db, entry_wait="2026-11-24T17:58:02+00:00 no free-feed print at or after the entry bar yet (its last "
                               "print 12:57:50 ET)")
    rep = rv.report(db, DAY)
    (i,) = rep.items
    assert (i.book, i.id, i.large, i.late) == ("short", rid, True, False)
    assert round(i.live.net, 4) == 0.0467 and round(i.settled.net, 4) == -0.2123       # the texted +4.7%, the settled -21.2%
    assert (i.live.reason, i.settled.reason) == ("60 min", "stop")
    assert round(i.live.stop, 3) == 1.224 and round(i.settled.stop, 2) == 1.20
    head, line = rep.lines
    assert head.startswith(f"✏️ {bold('revised at the settle')} (live estimate → settled result; listed at 5 points")
    assert italic("neither is a broker fill, and the execution model has its own line") in head
    assert "GLXG short: live estimate +4.7% → settled -21.2%." in line
    assert "Live paper path (free feed): short 13:00:32 $1.02, stop $1.224, out 14:00:37 $0.9598, 60 min." in line
    assert "Settled rule replay (official bars): short 12:58 $1.00, stop $1.20, out $1.20, stop." in line
    assert ("Cause: different entries, so different stop levels: official bars reached the replay's $1.20; the free "
            "feed never showed the live path's $1.224; the live entry came 2 min 33 s after the start of the replay's "
            "entry minute (no free-feed print at or after the entry bar yet (its last print 12:57:50 ET))") in line
    for word in ("fill at", "corrected fill", "model", "-20.5%", "+6.0%"):          # no model figure, no "fill" claim
        assert word not in line
    assert rep.through == "2026-11-25T01:40:01.567579+00:00"


def test_a_small_revision_says_nothing_and_a_same_exit_price_gap_names_the_prices(db):
    short(db, "JZ", est_entry=1.97, est_exit=1.53, entry=1.9692, cover=1.45, net=0.2462,
          est_entry_at="2026-11-24T15:23:22+00:00", entry_ts="2026-11-24 15:23:00+00:00")
    assert rv.report(db, DAY).lines == []
    short(db, "WIDE", message_id=2, est_entry=2.00, est_exit=1.90, entry=2.00, cover=1.70, net=0.13,
          est_entry_at="2026-11-24T15:23:22+00:00", entry_ts="2026-11-24 15:23:00+00:00")
    line = rv.report(db, DAY).lines[1]
    assert "WIDE short: live estimate" in line and "→ settled +13.0%" in line
    assert "Cause: the same exit, different prices: entry $2.00 live against $2.00, exit $1.90 against $1.70" in line


def test_a_cause_the_two_records_do_not_establish_is_unresolved(db):
    # live stopped at ITS level, the replay (same entry, same level) did not: nothing stored explains it
    short(db, "ODD", est_entry=1.00, est_exit=1.20, reason="stop", entry=1.0, cover=0.95, net=0.04)
    assert rv.report(db, DAY).lines[1].endswith("Cause: unresolved")
    # the official bars reached a level the free feed did not show, with the live level LOWER: said as that, no more
    short(db, "MPU", message_id=3, est_entry=0.99, est_exit=0.95, entry=1.0, cover=1.2, net=-0.21)
    assert "which is not higher" in rv.report(db, DAY).lines[2]


def test_a_live_estimate_that_was_itself_corrected_keeps_its_first_exit(db):
    rid = short(db, "MPU", est_entry=1.54, est_exit=1.848, reason="stop (official tape, seen late)", entry=1.5,
                cover=1.55, net=-0.045, est_exit_at="2026-11-24T15:54:00+00:00")
    db.decide("short", rid, None, "open", "est_closed", "60 min", None, est_exit=1.72, est_exit_at="2026-11-24T16:01:00+00:00")
    db.decide("short", rid, "MPU", "est_closed", "est_closed", "stop (official tape, seen late)", None, exit=1.848)
    line = rv.report(db, DAY).lines[1]
    assert "(first texted out $1.72 (60 min) at " in line and "then corrected)" in line
    assert "out 10:54:00 $1.848, stop (official tape, seen late)" in line          # the last live estimate is the one compared


def test_a_long_names_both_exits_and_a_text_order_settle_says_how_it_was_made(db):
    fid = short(db, "BENF", status="filtered")
    long(db, fid)                                                                    # BENF 11-24: +4.2% both ways
    assert rv.report(db, DAY).lines == []
    f2 = short(db, "GMM", status="filtered", message_id=5)
    lid = long(db, f2, "GMM", est_entry=1.785, est_exit=1.74, reason=text_order.REASON, entry=1.835, exit=1.875,
               exit_reason="target", net=0.0149)
    db.decide("long", lid, None, "open", "est_closed", "stop", None, est_exit=1.74)
    line = rv.report(db, DAY).lines[1]
    assert "GMM long: live estimate -3.3% → settled +1.5%." in line
    assert "Live paper path (free feed): buy 14:04:00 $1.785, stop $0.8949, out 14:33:00 $1.74, stop." in line
    assert "[settled as the order sent at the text (the tested order did not fill on official bars)]" in line
    assert "Cause: the same rule ended differently on the two feeds: stop on the free feed's bars, target on official bars" in line


def test_a_void_and_a_live_path_that_never_closed_are_listed_not_dropped(db):
    short(db, "VOID", status="void", reason="no trade on the full tape after the live entry (x)",
          settled_at="2026-11-25T01:40:00+00:00")
    short(db, "OPEN", message_id=6, est_exit=None, est_exit_at=None, reason="veto")   # settled while the live path was open
    a, b = rv.report(db, DAY).lines[1:]
    assert "VOID short: live estimate +4.7% → no settled result (void: no trade on the full tape" in a
    assert "OPEN short: live estimate ? → settled -21.2%." in b and "no exit on record" in b and b.endswith("unresolved")


def test_an_unsettled_trade_is_marked_and_a_late_settle_is_listed_once_by_the_next_report(db):
    rid = short(db, status="est_closed")
    rep = rv.report(db, DAY)
    assert rep.lines == ["⏳ not settled yet, so no revision can be stated: GLXG short live estimate +4.7%"]
    assert rep.through is None
    rv.mark(db, DAY, rep.through)                                    # nothing settled: no watermark is written
    assert db.flag(f"{rv.WATERMARK}:2026-11-24") is None
    nxt = date(2026, 11, 25)
    assert rv.report(db, nxt).lines == ["⏳ not settled yet, so no revision can be stated: GLXG short (11-24) live "
                                        "estimate +4.7%"]            # still waiting a day later: said, with its date
    with db.tx() as c:                                               # the settle catches up on 11-25 at 09:00 ET
        c.execute("UPDATE fade_trades SET status='settled', entry_ts=?, entry=1.0, cover=1.2, net=-0.2123386, "
                  "settled_at=? WHERE id=?", ("2026-11-24 17:58:00+00:00", "2026-11-25T14:00:00+00:00", rid))
    rep = rv.report(db, nxt)
    assert "GLXG short (session 11-24, settled late): live estimate +4.7% → settled -21.2%." in rep.lines[1]
    assert rv.report(db, nxt).lines == rep.lines                     # building tonight's report again: the same block
    rv.mark(db, nxt, rep.through)
    assert db.flag(f"{rv.WATERMARK}:2026-11-25") == "2026-11-25T14:00:00+00:00"
    assert rv.report(db, date(2026, 11, 27)).lines == []             # the report after it does not repeat it
    assert rv.report(db, nxt).lines == rep.lines                     # ... and 11-25's own report still reads the same


def test_a_late_settle_without_a_large_revision_is_still_reported_once(db):
    short(db, "CALM", session="2026-11-23", est_entry=2.0, est_exit=1.9, entry=2.0, cover=1.91, net=0.03,
          posted_at="2026-11-23T15:00:00+00:00", est_entry_at="2026-11-23T15:01:00+00:00",
          entry_ts="2026-11-23 15:01:00+00:00", settled_at="2026-11-24T14:00:00+00:00")
    rep = rv.report(db, DAY)                                         # no flag yet: the fallback mark is 11-23 20:50 ET
    assert len(rep.lines) == 1 and rep.lines[0].startswith("settled since the last report: CALM short (11-23) +3.0%")
    rv.mark(db, DAY, rep.through)
    assert rv.report(db, date(2026, 11, 25)).lines == []
    # a trade that settled on time on its own night is never repeated by a later report
    short(db, "OLD", session="2026-11-20", message_id=9, posted_at="2026-11-20T15:00:00+00:00",
          settled_at="2026-11-21T01:40:00+00:00")
    assert rv.report(db, date(2026, 11, 25)).lines == []


def test_the_nightly_report_carries_the_block_and_hands_back_the_mark(db):
    short(db)
    marks: dict = {}
    text = digest.build(db, datetime(2026, 11, 24, 20, 50, tzinfo=ET), fills=[], problems=[], broker=None, research=[],
                        marks=marks)
    head = bold("revised at the settle")
    assert head in text and "GLXG short: live estimate +4.7% → settled -21.2%." in text
    assert text.index(bold("TODAY")) < text.index(head) < text.index(bold("RECORD"))
    assert marks == {rv.WATERMARK: "2026-11-25T01:40:01.567579+00:00"}
    assert db.flag(f"{rv.WATERMARK}:2026-11-24") is None             # building is a read: only the job writes the mark
    assert "GLXG +4.7% → -21.2%" in summary.build(db, DAY)


def test_the_settled_exit_of_a_short_is_read_off_its_own_numbers():
    base = {"session": "2026-11-24", "entry_ts": "2026-11-24 17:58:00+00:00", "reason": "60 min"}
    assert fade.settled_exit({**base, "entry": 1.0, "cover": 1.2000000000000002}) == "stop"
    assert fade.settled_exit({**base, "entry": 1.0, "cover": 1.31}) == "stop"       # a minute that opened over the stop
    assert fade.settled_exit({**base, "entry": 1.0, "cover": 1.1999}) == "60 min"
    assert fade.settled_exit({**base, "entry": 1.0, "cover": 0.9, "entry_ts": "2026-11-24 20:30:00+00:00"}) == "close"
    assert fade.settled_exit({**base, "entry": 1.0, "cover": 0.9, "reason": fade.VETO_EXIT}) == fade.VETO_EXIT
    assert fade.settled_exit({**base, "entry": None, "cover": None}) is None
    assert datetime(2026, 11, 24, tzinfo=timezone.utc)               # (imports used)
