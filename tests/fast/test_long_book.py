"""Live paper long book (owner 2026-11-10 option A): tested setups traded live on paper, longs first."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.fast import fade, long_book as lb, long_shadow as ls
from sgt.fast import patterns as pt
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

POSTED = pd.Timestamp("2026-11-09 15:00:20", tz="UTC")          # 10:00:20 ET -> start bar 15:01
T1 = pd.Timestamp("2026-11-09 15:01", tz="UTC")


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def bars(rows, start=T1):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for i, (o, h, lo, c, v) in enumerate(rows)])


# red candle 15:02 (high 10.55, low 10.20); 15:03 trades through 10.55; 15:04 runs to 11.60
DAY = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
            (10.65, 11.6, 10.6, 11.5, 800)] + [(11.5, 11.6, 11.4, 11.5, 100)] * 50)


def alert_row(db, sym="ABCD", status="pending", reason=None, posted=POSTED):
    with db.tx() as c:
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                         " reason, weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (sym, "2026-11-09", "volume_spike", posted.isoformat(), "-1", hash(sym) % 10_000, status,
                          reason, 0.05, posted.isoformat())).lastrowid


def trades(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM long_trades ORDER BY id")]


def at(minute, second=0):
    return (T1 + pd.Timedelta(minutes=minute, seconds=second)).to_pydatetime()


def feed(day):
    return lambda sym, now: day                                  # the full day: the book must use only closed bars


def test_first_long_to_fill_with_tie_order_and_honest_text(db):
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(DAY), sent.append, now=at(2, 30)) == 0 and trades(db) == []   # 15:03 bar not closed
    assert lb.tick(db, feed(DAY), sent.append, now=at(3)) == 1
    (t,) = trades(db)
    assert (t["kind"], t["est_entry"], t["stop"], t["status"]) == ("L4b", 10.55, 10.2, "open")   # L4b beats L4a (tie)
    assert t["target"] == pytest.approx(10.55 + 2 * 0.35)
    assert sent[0].startswith("[SGT] 🟢 LONG ABCD @ $10.55") and "red-candle break" in sent[0]
    assert "Tested on past alerts: -2.4%/trade over 754 trades" in sent[0]
    assert lb.tick(db, feed(DAY), sent.append, now=at(3, 30)) == 0 and len(trades(db)) == 1      # one long per alert


def test_live_book_equals_the_research_simulator(db):
    """Minute by minute on closed bars, the live entry and exit are exactly the study's on the full day."""
    alert_row(db)
    sent = []
    for k in range(0, 70 * 2):
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k))
    (t,) = trades(db)
    s = [x for x in ls.setups(DAY, {"posted_at": POSTED.isoformat(), "status": "live"})[0] if x.kind == "L4b"][0]
    ref = pt.simulate(DAY, s, latest=ls._latest("2026-11-09"))
    assert (t["status"], t["est_entry"], t["reason"]) == ("est_closed", ref.entry, ref.reason)
    assert t["est_exit"] == pytest.approx(ref.exit) and t["est_exit_at"] == ref.exit_ts.isoformat()
    assert len(sent) == 2 and sent[1].startswith("[SGT] 🟢 LONG OUT ABCD · made +6.6%")


def test_time_exit_waits_for_the_whole_hold(db):
    flat = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400)] + [(10.6, 10.65, 10.4, 10.6, 100)] * 60)
    alert_row(db)
    sent = []
    lb.tick(db, feed(flat), sent.append, now=at(4))
    (t,) = trades(db)
    entry_ts = pd.Timestamp(t["entry_ts"])
    lb.tick(db, feed(flat), sent.append, now=(entry_ts + pd.Timedelta(minutes=20)).to_pydatetime())
    assert trades(db)[0]["status"] == "open"                                  # not final before the hold ends
    lb.tick(db, feed(flat), sent.append, now=(entry_ts + pt.HOLD).to_pydatetime())
    t = trades(db)[0]
    assert (t["status"], t["reason"]) == ("est_closed", "time")
    assert pd.Timestamp(t["est_exit_at"]) == entry_ts + pt.HOLD - pt.BAR


def test_longs_on_alerts_the_short_skipped_and_not_on_others(db):
    alert_row(db, "NEWS", "filtered", fade.NEWS_REASON)
    alert_row(db, "BETA", "filtered", fade.BETA_REASON)
    alert_row(db, "WIN", "filtered", "outside 09:30-15:44")
    lb.tick(db, feed(DAY), [].append, now=at(3))
    assert {t["symbol"] for t in trades(db)} == {"NEWS", "BETA"}


def test_pause_and_disarm_block_new_longs(db):
    alert_row(db)
    db.set_flag("paused", "1")
    lb.tick(db, feed(DAY), [].append, now=at(3))
    assert trades(db) == []
    db.set_flag("paused", "0")
    ap.disarm(db, now=at(3))
    lb.tick(db, feed(DAY), [].append, now=at(3, 10))
    assert trades(db) == []


def test_halt_reopen_live_uses_l7(db):
    alert_row(db, "HALT", "skipped", "no trade print within 5 min (halted or no quote)")
    reopen = POSTED.floor("min") + pd.Timedelta(minutes=9)
    day = bars([(10.0, 10.5, 10.0, 10.4, 1), (10.5, 10.55, 10.2, 10.3, 1), (10.3, 11.0, 10.3, 10.9, 1)]
               + [(10.9, 11.0, 10.8, 10.9, 1)] * 40, start=reopen)
    lb.tick(db, feed(day), [].append, now=(reopen + 3 * pt.BAR).to_pydatetime())
    (t,) = trades(db)
    assert t["kind"] == "L7b"


def test_no_setup_window_closes_with_a_marker(db):
    alert_row(db, "FLAT")
    flat = bars([(10.0, 10.1, 9.9, 10.05, 1)] * 80)                      # never red
    lb.tick(db, feed(flat), [].append, now=at(10))
    assert trades(db) == []                                               # still inside the window: keep watching
    lb.tick(db, feed(flat), [].append, now=at(80))
    assert [(t["status"], t["kind"]) for t in trades(db)] == [("none", None)]


NIGHT = datetime(2026, 11, 10, 1, 40, tzinfo=timezone.utc)      # 20:40 ET on 2026-11-09: the settle job
COST = 0.01


def sip(days: dict):
    """Official SIP bars per symbol (the settle job's loader); unknown symbol = no bars yet."""
    return lambda sym, session: days.get(sym, pd.DataFrame())


def cost(sym, ts, px):
    return COST


def live_long(db, sym="ABCD"):
    """A live L4b long on DAY (IEX): buy-stop 10.55 filled on the 15:03 bar, stop 10.20, then closed."""
    fid = alert_row(db, sym)
    for k in range(0, 140):
        lb.tick(db, feed(DAY), [].append, now=at(0, 30 * k))
    (t,) = [x for x in trades(db) if x["symbol"] == sym]
    assert (t["kind"], t["trigger"], t["stop"], t["status"]) == ("L4b", 10.55, 10.2, "est_closed")
    return fid, t


def test_settle_replays_the_live_order_on_sip_bars(db):
    """Owner 2026-11-11 "real long scoring": the official result is the live order (fill bar, trigger, stop) on SIP,
    the 2R target from the SIP fill; never the shadow's re-detected setup."""
    fid, _ = live_long(db)
    with db.tx() as c:                                   # the shadow re-detected a different, winning trade
        c.execute("INSERT INTO long_shadow(fade_id, symbol, session, kind, side, net, entry, created_at) VALUES "
                  "(?,?,?,?,?,?,?,?)", (fid, "ABCD", "2026-11-09", "L4b", "long", 0.30, 10.0, POSTED.isoformat()))
    assert lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT) == ["ABCD L4b +5.64%"]
    (t,) = trades(db)
    tgt = 10.55 + pt.RR * (10.55 - 10.2)                 # 11.25, hit on the 15:04 bar (high 11.6, open 10.65)
    assert (t["status"], t["entry"], t["exit"], t["exit_reason"]) == ("settled", 10.55, pytest.approx(tgt), "target")
    assert t["fill_ts"] == (T1 + 2 * pt.BAR).isoformat() and t["cost_rt"] == COST
    assert t["net"] == pytest.approx(tgt / 10.55 - 1 - COST) and t["settled_at"] == NIGHT.isoformat()
    assert lb.nav(db) == pytest.approx(100_000 * (1 + 0.05 * t["net"]))
    assert "long book (paper) 1 trades, avg +5.64%/trade" in lb.record_line(db)
    assert lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT) == []          # settled once


def test_live_loss_stays_a_loss_when_sip_would_have_found_a_winner(db):
    """KULR 11-10: live IEX trigger stopped out (-1.2%), the shadow's SIP re-detection won (+1.63%). The live order on
    SIP: the stop is hit, so the official result is a loss."""
    live_long(db)
    flush = DAY.copy()
    flush.loc[3, ["open", "high", "low", "close"]] = [10.5, 10.6, 10.1, 10.15]   # 15:04 trades through the 10.20 stop
    assert lb.settle(db, sip({"ABCD": flush}), cost, now=NIGHT) == [f"ABCD L4b {10.2 / 10.55 - 1 - COST:+.2%}"]
    assert trades(db)[0]["exit_reason"] == "stop" and trades(db)[0]["net"] < 0


def test_order_that_never_fills_on_sip_is_void(db):
    live_long(db)
    thru = DAY.copy()
    thru.loc[2:8, ["open", "high", "low", "close"]] = [10.1, 10.15, 10.0, 10.1]   # opens through the stop: cancelled
    assert lb.settle(db, sip({"ABCD": thru}), cost, now=NIGHT) == ["ABCD L4b void"]
    assert (trades(db)[0]["status"], trades(db)[0]["reason"]) == ("void", lb.NO_FILL)


def test_settle_waits_for_complete_sip_bars(db):
    live_long(db)
    early = datetime(2026, 11, 9, 21, 10, tzinfo=timezone.utc)               # 16:10 ET: SIP still 15 minutes late
    assert lb.settle(db, sip({"ABCD": DAY}), cost, now=early) == [] and trades(db)[0]["status"] == "est_closed"


def test_legacy_shadow_settled_long_is_rescored_once(db):
    """Rows settled before 2026-11-11 (official = shadow re-detection, no official entry stored) are re-scored."""
    live_long(db)
    with db.tx() as c:
        c.execute("UPDATE long_trades SET status='settled', net=0.0163")
    (line,) = lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT)
    assert line == "ABCD L4b +5.64% (re-scored, was +1.63%)" and trades(db)[0]["entry"] == 10.55
    assert lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT) == []


def test_one_failing_long_never_blocks_the_others(db, caplog):
    live_long(db, "BAD")
    live_long(db, "GOOD")

    def bars_for(sym, session):
        if sym == "BAD":
            raise RuntimeError("sip down")
        return DAY
    assert lb.settle(db, bars_for, cost, now=NIGHT) == ["GOOD L4b +5.64%"]
    assert "official result failed" in caplog.text
    assert {t["symbol"]: t["status"] for t in trades(db)} == {"BAD": "est_closed", "GOOD": "settled"}


def test_digest_lists_longs_first(db):
    from zoneinfo import ZoneInfo

    from sgt.ops import digest
    alert_row(db)
    lb.tick(db, feed(DAY), [].append, now=at(3))
    text = digest.build(db, datetime(2026, 11, 9, 20, 50, tzinfo=ZoneInfo("America/New_York")), fills=[],
                        problems=[], broker=None, research=[])
    lines = text.splitlines()
    from sgt.ops.sms_style import bold
    i = next(k for k, ln in enumerate(lines) if bold("TODAY") in ln)
    assert bold("longs") in lines[i + 1] and "(paper) 1 trades: ABCD red-candle break open" in lines[i + 1]


def test_a_failing_alert_never_stops_the_book(db):
    alert_row(db, "BAD")
    alert_row(db, "GOOD")

    def bars_for(sym, now):
        if sym == "BAD":
            raise RuntimeError("iex down")
        return DAY
    assert lb.tick(db, bars_for, [].append, now=at(3)) == 1 and [t["symbol"] for t in trades(db)] == ["GOOD"]


def test_at_utc_now_default_runs(db):
    assert lb.tick(db, feed(DAY), [].append, now=datetime(2026, 11, 9, 21, 0, tzinfo=timezone.utc)) == 0


def test_the_long_book_never_takes_the_short_setup(monkeypatch):
    short = pt.Setup("S2", "short", T1 + pt.BAR, 10.2, 10.6)
    monkeypatch.setattr(ls, "setups", lambda f, row: ([short], T1))
    day = bars([(10.3, 10.4, 10.0, 10.1, 1)] * 10)                       # the short would fill at once
    assert lb.first_fill(day, {"posted_at": POSTED.isoformat(), "status": "pending"}, ls._latest("2026-11-09")) is None


def test_repeat_alerts_of_one_stock_are_one_event(db):
    """Replay 2026-11-09: WCT was alerted five times (each skipped by the short for news) and got five longs."""
    for k in range(4):
        alert_row(db, "WCT", "filtered", fade.NEWS_REASON, posted=POSTED + pd.Timedelta(seconds=k))
    alert_row(db, "WCT", "filtered", "outside 09:30-15:44", posted=POSTED - pd.Timedelta(minutes=1))   # not an event
    for k in range(0, 140):
        lb.tick(db, feed(DAY), [].append, now=at(0, 30 * k))
    assert [t["symbol"] for t in trades(db) if t["status"] != "none"] == ["WCT"]
    with db.tx() as c:
        first = c.execute("SELECT MIN(id) FROM fade_trades WHERE reason=?", (fade.NEWS_REASON,)).fetchone()[0]
    assert {t["fade_id"] for t in trades(db)} == {first}
    ls.run(db, feed(DAY), lambda *a: 0.0, now=at(400))
    with db.tx() as c:
        assert {r[0] for r in c.execute("SELECT DISTINCT fade_id FROM long_shadow")} == {first}   # same event


def _closed_long(db, kind="L7b", session="2026-11-09"):
    fid = alert_row(db, "ORPH")
    with db.tx() as c:
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, target, weight, entry_ts, "
                  "est_entry, est_exit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, "ORPH", session, kind, "est_closed", 9.0, 12.0, 0.05, T1.isoformat(), 10.0, 11.0,
                   POSTED.isoformat()))
    return fid


def test_kind_mismatch_with_the_shadow_no_longer_voids_a_live_long(db):
    """Before 2026-11-11 a live L7 (IEX gap) with a regular event on SIP was void. Now the live order itself is
    scored on SIP, whatever setup the shadow found."""
    fid = _closed_long(db)
    with db.tx() as c:
        c.execute("INSERT INTO long_shadow(fade_id, symbol, session, kind, side, net, created_at) VALUES "
                  "(?,?,?,?,?,?,?)", (fid, "ORPH", "2026-11-09", "L4a", "long", -0.02, POSTED.isoformat()))
    flat = bars([(10.0, 10.1, 9.9, 10.0, 1)] * 80)                     # market entry at 10.00, time exit at 10.00
    assert lb.settle(db, sip({"ORPH": flat}), cost, now=NIGHT) == [f"ORPH L7b {-COST:+.2%}"]
    t = [x for x in trades(db) if x["symbol"] == "ORPH"][0]
    assert (t["status"], t["entry"], t["exit_reason"]) == ("settled", 10.0, "time")


def test_long_with_no_official_bars_is_void_after_a_few_days(db):
    _closed_long(db, "L4a", session="2026-11-09")
    assert lb.settle(db, sip({}), cost, now=NIGHT) == []                     # an outage day: retried next night
    later = datetime(2026, 11, 13, 2, 0, tzinfo=timezone.utc)
    assert lb.settle(db, sip({}), cost, now=later) == ["ORPH L4a void"]
    assert [x["reason"] for x in trades(db) if x["symbol"] == "ORPH"] == ["no official SIP bars"]


def test_digest_line_says_void(db):
    _closed_long(db)
    with db.tx() as c:
        c.execute("UPDATE long_trades SET status='void'")
    assert "ORPH halt reopen: red-candle break void" in lb.today_line(db, "2026-11-09")


def test_long_book_settle_failure_is_logged_separately(tmp_path, monkeypatch, caplog):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fade_live as fl

    monkeypatch.setattr(fl, "OpsStore", lambda: OpsStore(tmp_path / "ops.db"))
    monkeypatch.setattr(fl.fade, "settle", lambda db, official: [])
    monkeypatch.setattr(fl, "settle_scanner", lambda db: [])
    monkeypatch.setattr(fl, "settle_long_shadow", lambda db: ["X L4a +1.00%"])

    monkeypatch.setattr(fl, "sip_session_tools", lambda: (sip({}), cost))

    def boom(db, bars, cost_fn):
        raise RuntimeError("bad join")
    monkeypatch.setattr(lb, "settle", boom)
    assert fl.cmd_settle() == 0
    assert "long book settle failed" in caplog.text and "long shadow failed" not in caplog.text


def test_open_long_from_an_earlier_session_gets_its_official_result(db):
    """Final pass F-1: listener down across the close; the next day the long gets its official result, not 'open'."""
    _closed_long(db, "L4a")
    with db.tx() as c:
        c.execute("UPDATE long_trades SET status='open'")
    flat = bars([(10.0, 10.1, 9.9, 10.0, 1)] * 80)
    assert lb.settle(db, sip({"ORPH": flat}), cost, now=at(60)) == []           # same session: still trading
    nxt = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)
    assert lb.settle(db, sip({"ORPH": flat}), cost, now=nxt) == [f"ORPH L4a {-COST:+.2%}"]
    t = [x for x in trades(db) if x["symbol"] == "ORPH"][0]
    assert t["status"] == "settled" and "listener missed the exit" in t["reason"]


def test_long_dollars_use_the_long_book(db):
    """Final pass F-2: entry and exit dollars come from the long book's value, not the short book's."""
    with db.tx() as c:                                      # the short book lost 50%: its NAV must not drive longs
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, weight,"
                  " net, settled_at, created_at) VALUES ('L','2026-11-02','volume_spike','x','-1',9,'settled',1.0,-0.5,"
                  "'2026-11-02T21:00:00+00:00','x')")
    assert fade.nav(db) == pytest.approx(50_000)
    alert_row(db)
    sent = []
    for k in range(0, 140):
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k))
    assert "$5,000 (paper)" in sent[0]
    assert "(+$332 est, paper)" in sent[1]                                 # 0.05 x 100,000 x +6.64%


def test_a_fill_found_after_a_restart_is_missed_not_traded(db):
    """2026-11-10: after the 11:45 deploy the book booked KULR's 10:39 fill and texted entry and exit at once. A fill
    older than LIVE_LAG was not seen live: recorded as 'missed', no text, never opened, not in the digest."""
    alert_row(db)
    sent = []
    fill_bar = T1 + pd.Timedelta(minutes=2)                      # L4b fills in the 15:03 bar
    late = (fill_bar + lb.LIVE_LAG + pd.Timedelta(seconds=1)).to_pydatetime()
    assert lb.tick(db, feed(DAY), sent.append, now=late) == 0 and sent == []
    (t,) = trades(db)
    assert (t["status"], t["kind"], t["est_entry"]) == ("missed", "L4b", 10.55)
    assert lb.tick(db, feed(DAY), sent.append, now=at(40)) == 0 and len(trades(db)) == 1     # never retried
    assert lb.today_line(db, "2026-11-09") is None and lb.record_line(db) is None
    alert_row(db, sym="EFGH")
    on_time = (fill_bar + lb.LIVE_LAG).to_pydatetime()            # the edge still counts as live
    assert lb.tick(db, feed(DAY), sent.append, now=on_time) == 1 and sent[0].startswith("[SGT] 🟢 LONG EFGH @ $10.55")


def test_live_exit_sells_at_the_target_on_the_fill_bar():
    """The live estimate uses the same fill-bar rule as the official replay (GEVO 2026-11-11: $1.975 -> $1.96)."""
    t0 = pd.Timestamp("2026-11-11T14:35:00Z")
    f = pd.DataFrame({"ts": [t0, t0 + pd.Timedelta(minutes=1)], "open": [1.91, 1.975], "high": [1.97, 2.0],
                      "low": [1.905, 1.97], "close": [1.96, 1.99], "volume": [1000, 1000]})
    r = {"kind": "L4b", "est_entry": 1.92, "stop": 1.90, "target": 1.96, "trigger": 1.92}
    t = lb._exit(f, r, t0)
    assert (t.reason, t.exit_ts) == ("target", t0) and t.exit == pytest.approx(1.96)
