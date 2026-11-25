"""Live paper long book (owner 2026-11-10 option A): tested setups traded live on paper, longs first."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.fast import est_costs, fade, long_book as lb, long_shadow as ls
from sgt.fast import patterns as pt
from sgt.ops import approvals as ap
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore

POSTED = pd.Timestamp("2026-11-09 15:00:20", tz="UTC")          # 10:00:20 ET -> start bar 15:01
TEST_W = lb.PROBE_USD / 100_000                                 # 2026-11-19: an unproven book trades at test size
T1 = pd.Timestamp("2026-11-09 15:01", tz="UTC")


LEGACY_KINDS = ("L5v2", "L5", "L4b", "L6", "L4a", "L7b", "L7a")


@pytest.fixture(autouse=True)
def one_minute_setups(monkeypatch):
    """These tests drive the book's machinery (fills, exits, texts, settle) through the one-minute setups it traded
    until 2026-11-19. The live default since then (base break) has its own file: tests/fast/test_long_base.py."""
    from sgt.fast import long_book, patterns

    monkeypatch.setattr(long_book, "LIVE_KINDS", LEGACY_KINDS)
    monkeypatch.setattr(long_book, "LIVE_WATCH", patterns.WATCH)


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
    # owner 2026-11-20 ("why are the texts different"): the long card is laid out like the fade's entry card
    assert sent[0].split("\n") == [
        f"[SGT] ✅ {bold('LONG IN')} · {bold('BUY ABCD')} @ {bold('$10.55')}",
        "💬 Bought $404 of ABCD: we make money if it rises",
        "🎲 red-candle break: " + italic("avg -2.4%/trade over 754 past trades") + " (live paper test)",
        "    " + italic("Past figures: a replay of past alerts on candles, no real fills. Averages, not a forecast for "
                        "this trade."),
        "💵 $404 (paper, test size; full size $4,038) (sized to the stop)",
        "🛑 stop $10.20 (-3.3%; a trigger, not a cap) ≈ -$17 · 🎯 target $11.25 (+6.6%) · ⏰ out 10:33 ET",
        "🔗 https://www.tradingview.com/chart/?symbol=ABCD"]
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
    # 2026-11-16: the texted estimate is after the estimated round-trip spread (+6.6% move, 0.8% costs)
    assert len(sent) == 2 and sent[1].split("\n") == [                                  # at test size
        f"[SGT] 🟢 {bold('LONG OUT')} · {bold('ABCD')} · made {bold('+$24 (+5.9%)')}",
        "Bought $10.55 → sold $11.25 · 🎯 target hit", "💡 Why: price rose 6.6% while we were long",
        f"📅 Today: 1 long, {bold('+$24')}", "📈 https://www.tradingview.com/chart/?symbol=ABCD",
        italic("Paper, estimate after about 0.8% costs (spread); official numbers in the midnight summary.")]


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
    w = TEST_W * min(1.0, lb.RISK_D50 / ((10.55 - 10.2) / 10.55))   # A2: sized to the 3.3% stop (0.81x), test size
    assert t["weight"] == pytest.approx(w) and t["rules"] == lb.RULES
    assert lb.nav(db) == pytest.approx(100_000 * (1 + w * t["net"]))
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


def test_a_texted_long_whose_order_never_fills_on_sip_is_not_cut(db):
    """Owner 2026-11-18: "you can't cut trades that already happened in real life". It was void until then; now it
    waits (still a trade, with its estimate) for the order at the text, and that books its official result."""
    from sgt.fast import text_order

    live_long(db)
    thru = DAY.copy()
    thru.loc[2:8, ["open", "high", "low", "close"]] = [10.1, 10.15, 10.0, 10.1]   # opens through the stop: cancelled
    assert lb.settle(db, sip({"ABCD": thru}), cost, now=NIGHT) == ["ABCD L4b not filled on the full tape: scored as bought at the text"]
    assert (trades(db)[0]["status"], trades(db)[0]["reason"]) == ("est_closed", f"{lb.NO_FILL}; live exit target")
    assert lb.settle(db, sip({"ABCD": thru}), cost, now=NIGHT) == []              # said once; nothing changes meanwhile
    assert "void" not in lb.today_line(db, "2026-11-09")
    got = text_order.run(db, lambda sym, a, b: None, sip({"ABCD": thru}), cost, now=NIGHT)
    assert got == [] and trades(db)[0]["status"] == "est_closed"                   # prints request failed: asked again
    got = text_order.run(db, lambda sym, a, b: text_order.NO_PRINTS, sip({"ABCD": thru}), cost, now=NIGHT)
    (t,) = trades(db)
    assert len(got) == 1 and t["status"] == "settled" and t["reason"] == text_order.REASON
    assert t["entry"] == 10.1 and t["exit"] == 10.1 and t["net"] == pytest.approx(-COST)   # bought under the stop: out at once
    assert lb.nav(db) == pytest.approx(fade.NAV0 * (1 + t["weight"] * t["net"]))


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
    w = TEST_W * min(1.0, lb.RISK_D50 / ((10.55 - 10.2) / 10.55))         # A2 risk size inside the $500 test size
    assert f"${w * 100_000:,.0f} (paper, test size; full size $4,038) (sized to the stop)" in sent[0]
    c = est_costs.cost("long", "ABCD", trades(db)[0]["entry_ts"], 10.55)
    assert c == pytest.approx(0.0077, abs=5e-5)                           # first-hour round-trip spread at $10.55
    assert "made " + bold(f"+${w * 100_000 * (11.25 / 10.55 - 1 - c):,.0f} (") in sent[1]          # w x 100,000 x net


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
    assert lb.tick(db, feed(DAY), sent.append, now=on_time) == 1 and sent[0].startswith(f"[SGT] ✅ {bold('LONG IN')} · {bold('BUY EFGH')} @ {bold('$10.55')}")


def test_live_exit_sells_at_the_target_on_the_fill_bar():
    """The live estimate uses the same fill-bar rule as the official replay (GEVO 2026-11-11: $1.975 -> $1.96)."""
    t0 = pd.Timestamp("2026-11-11T14:35:00Z")
    f = pd.DataFrame({"ts": [t0, t0 + pd.Timedelta(minutes=1)], "open": [1.91, 1.975], "high": [1.97, 2.0],
                      "low": [1.905, 1.97], "close": [1.96, 1.99], "volume": [1000, 1000]})
    r = {"kind": "L4b", "est_entry": 1.92, "stop": 1.90, "target": 1.96, "trigger": 1.92}
    t = lb._exit(f, r, t0)
    assert (t.reason, t.exit_ts) == ("target", t0) and t.exit == pytest.approx(1.96)


# ---- 2026-11-12 A2 rules (prereg_long_risk addendum A2): risk size + red-fill-candle exit -------------------------
# 15:03 trades through the 10.55 buy-stop but CLOSES 10.45 (red fill candle, no stop at 10.20, no 11.25 target);
# 15:04 opens 10.40 -> the A2 exit sells there. Without A2 the trade runs on and hits the target at 15:05.
RED_DAY = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.45, 500),
                (10.40, 10.6, 10.35, 10.5, 300), (10.5, 11.6, 10.5, 11.5, 800)] + [(11.5, 11.6, 11.4, 11.5, 100)] * 50)


def test_risk_weight_caps_at_one_and_shrinks_wide_stops():
    assert lb.risk_weight(10.0, 9.9) == 1.0                                   # 1% stop: tighter than the median
    assert lb.risk_weight(10.0, 9.0) == pytest.approx(lb.RISK_D50 / 0.10)     # 10% stop: 0.27x
    assert lb.risk_weight(10.0, 10.0) == 0.0 and lb.risk_weight(10.0, 10.5) == 0.0 and lb.risk_weight(0.0, -1) == 0.0


def test_a2_long_sells_at_the_next_open_after_a_red_fill_candle(db):
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(RED_DAY), sent.append, now=at(3)) == 1                # filled on the 15:03 bar
    (t,) = trades(db)
    assert (t["kind"], t["est_entry"], t["rules"]) == ("L4b", 10.55, lb.RULES)
    lb.tick(db, feed(RED_DAY), sent.append, now=at(3, 30))                        # 15:04 not closed: still open
    assert trades(db)[0]["status"] == "open"
    lb.tick(db, feed(RED_DAY), sent.append, now=at(4))
    (t,) = trades(db)
    assert (t["status"], t["reason"], t["est_exit"]) == ("est_closed", "red", 10.40)
    assert "fill candle closed red, out at the next open" in sent[-1] and "Bought $10.55 → sold $10.40" in sent[-1]


def test_a_long_opened_before_a2_keeps_its_old_exit_live_and_official(db):
    """Trades opened before the deploy (rules NULL) are never re-judged by the new exit."""
    r = {"kind": "L4b", "est_entry": 10.55, "stop": 10.2, "target": 11.25, "trigger": 10.55, "rules": None}
    t = lb._exit(lb.rth(RED_DAY, at(60)), r, T1 + 2 * pt.BAR)
    assert t.reason == "target"                                               # the old rule: target on 15:05
    t2 = lb._exit(lb.rth(RED_DAY, at(60)), {**r, "rules": lb.RULES}, T1 + 2 * pt.BAR)
    assert (t2.reason, t2.exit) == ("red", 10.40)


def test_a_stopped_long_card_reads_like_fade_out_and_adds_up_the_day(db, monkeypatch):
    # owner 2026-11-20 (SVRE): result first, the two prices and why, what the price did, the day so far, the links.
    monkeypatch.setattr(lb.chartserver, "link", lambda: "https://example.invalid/page/")
    at3 = (T1 + 2 * pt.BAR).isoformat()
    for sym, status, entry, exit_px in (("WINR", "est_closed", 10.0, 10.5), ("LOSR", "est_closed", 1.28, 1.19),
                                        ("OPEN", "open", 5.0, None), ("OTHR", "est_closed", 10.0, 20.0)):
        fid = alert_row(db, sym)
        with db.tx() as c:
            c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, weight, entry_ts, est_entry, "
                      "est_exit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (fid, sym, "2026-11-10" if sym == "OTHR" else "2026-11-09", "L8", status, 1.19, 0.002, at3, entry,
                       exit_px, POSTED.isoformat()))
    r = {"symbol": "LOSR", "weight": 0.002, "session": "2026-11-09", "entry_ts": at3, "stop": 1.20}
    t = pt.Trade("L8", "long", T1 + 2 * pt.BAR, 1.28, T1 + 4 * pt.BAR, 1.19, "stop", 1.19 / 1.28 - 1)
    c_lo, c_hi = (est_costs.cost("long", sym, at3, px) for sym, px in (("LOSR", 1.28), ("WINR", 10.0)))
    net = 1.19 / 1.28 - 1 - c_lo
    usd = 0.002 * lb.nav(db) * net
    day = usd + 0.002 * lb.nav(db) * (0.05 - c_hi)                          # the open long and another day's are not in it
    # owner 2026-11-25: never "loss capped". The stop's loss in its parts (the row's stop is 1.20: the sale at 1.19 is
    # 0.8 points past it), and a stop that is a trigger, not a cap.
    stop_part = [f"🛑 Stop $1.20 triggered: intended price risk {(1.20 - 1.28) / 1.28:+.1%}. A stop is a trigger, not a cap.",
                 f"    Estimated exit $1.19: 0.8 points beyond the stop · about {c_lo * 100:.1f} points of costs"]
    assert lb.exit_text(db, r, t).split("\n") == [
        f"[SGT] 🛑 {bold('LONG OUT')} · {bold('LOSR')} · lost {bold(f'-${abs(usd):,.0f} ({net:+.1%})')}",
        "Bought $1.28 → sold $1.19 · 🛑 stop triggered", "💡 Why: price fell 7.0% while we were long",
        *stop_part,
        f"📅 Today: 2 longs, {bold(lb._usd(day))}", "📈 https://www.tradingview.com/chart/?symbol=LOSR",
        "🖼 All trades: https://example.invalid/page/",
        italic(f"Paper, estimate after about {c_lo:.1%} costs (spread); official numbers in the midnight summary.")]
    monkeypatch.setattr(lb.chartserver, "link", lambda: "")
    flat = pt.Trade("L8", "long", T1 + 2 * pt.BAR, 1.28, T1 + 4 * pt.BAR, 1.28, "time", 0.0)
    out = lb.exit_text(db, {"symbol": "LOSR", "weight": 0.002, "entry_ts": at3}, flat)       # no session: no day line
    assert "All trades" not in out and "Today" not in out and "price did not move" in out and "⏰ time exit" in out
    assert out.startswith(f"[SGT] 🔴 {bold('LONG OUT')}") and lb._usd(0) == "+$0" and lb._usd(-1234.4) == "-$1,234"


def test_settle_applies_the_red_exit_only_to_a2_trades(db):
    for sym, rules in (("NEWR", lb.RULES), ("OLDR", None)):
        fid = alert_row(db, sym)
        with db.tx() as c:
            c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, "
                      "entry_ts, est_entry, est_last, created_at, rules) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (fid, sym, "2026-11-09", "L4b", "est_closed", 10.55, 10.2, 11.25, 0.04,
                       (T1 + 2 * pt.BAR).isoformat(), 10.55, 10.4, POSTED.isoformat(), rules))
    out = lb.settle(db, sip({"NEWR": RED_DAY, "OLDR": RED_DAY}), cost, now=NIGHT)
    got = {t["symbol"]: (t["exit_reason"], t["exit"]) for t in trades(db)}
    assert got["NEWR"] == ("red", 10.40) and got["OLDR"] == ("target", pytest.approx(11.25)) and len(out) == 2


def test_exit_text_never_fails_on_an_unknown_reason(db):
    t = pt.Trade("L4b", "long", T1, 10.0, T1, 9.9, "something-new", -0.01)
    assert "something-new" in lb.exit_text(db, {"symbol": "X", "weight": 0.05}, t)


def test_long_size_follows_the_drift_scale(db):
    from sgt.learning import live_monitor as lm
    db.set_flag(lm.SCALE_FLAG + lb.BOOK, "0.5")
    alert_row(db)
    lb.tick(db, feed(DAY), [].append, now=at(3))
    w = TEST_W * min(1.0, lb.RISK_D50 / ((10.55 - 10.2) / 10.55)) * 0.5
    assert trades(db)[0]["weight"] == pytest.approx(w)


@pytest.mark.parametrize("flag, n", [(None, 1), ("0", 1), ("1", 0), ("yes", 0)])
def test_long_off_opens_nothing_and_texts_the_setup_as_seen(db, flag, n):
    """Owner 2026-11-13 (measure-only) and 2026-11-17 (decision 1B: one "seen, not traded" text): while off no long
    is opened, the one it would have opened is recorded as seen and texted once; absent / "0" is unchanged."""
    alert_row(db)
    if flag is not None:
        db.set_flag(lb.OFF_FLAG, flag)
    sent = []
    assert lb.tick(db, feed(DAY), sent.append, now=at(3)) == n
    (t,) = trades(db)
    assert t["status"] == ("open" if n else "seen") and len(sent) == 1
    assert ("LONG SEEN, not traded: ABCD @ $10.55" in sent[0]) == (n == 0)
    assert (bold("BUY ABCD") in sent[0]) == (n == 1)


def test_a_seen_long_is_never_a_trade_and_is_scored_for_the_record(db):
    alert_row(db)
    db.set_flag(lb.OFF_FLAG, "1")
    sent = []
    lb.tick(db, feed(DAY), sent.append, now=at(3))
    (t,) = trades(db)
    assert (t["kind"], t["est_entry"], t["stop"], t["status"], t["rules"]) == ("L4b", 10.55, 10.2, "seen", "A2")
    assert t["weight"] == pytest.approx(TEST_W * lb.risk_weight(10.55, 10.2))        # the size it would have had
    assert "no position, not in the totals" in sent[0] and "stop $10.20" in sent[0] and "would be out by" in sent[0]
    lb.tick(db, feed(DAY), sent.append, now=at(50))                                   # nothing to exit, no second text
    assert [x["status"] for x in trades(db)] == ["seen"] and len(sent) == 1
    assert lb.today_line(db, "2026-11-09") is None and lb.record_line(db) is None and lb.nav(db) == fade.NAV0
    assert lb.seen_line(db, "2026-11-09") == "longs seen, not traded (1; not in the total): ABCD red-candle break"
    # the nightly settle scores what it would have made on official bars; the row stays "seen"
    out = lb.settle(db, sip({"ABCD": DAY}), cost, now=datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc))
    (t,) = trades(db)
    assert t["status"] == "seen" and t["net"] is not None and t["settled_at"] is not None and t["entry"] == 10.55
    assert out == [f"ABCD L4b seen, not traded: would have made {t['net']:+.2%}"]
    assert lb.nav(db) == fade.NAV0 and lb.record_line(db) is None                     # still in no total
    assert lb.seen_line(db, "2026-11-09").endswith(f"ABCD red-candle break {t['net']:+.1%}")
    assert lb.settle(db, sip({"ABCD": DAY}), cost, now=datetime(2026, 11, 10, 3, 0, tzinfo=timezone.utc)) == []


def test_a_seen_long_that_never_fills_on_official_bars_is_closed_once(db):
    alert_row(db)
    db.set_flag(lb.OFF_FLAG, "1")
    lb.tick(db, feed(DAY), [].append, now=at(3))
    flat = bars([(10.0, 10.1, 9.9, 10.0, 100)] * 60)                                  # official tape never trades 10.55
    now = datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)
    assert lb.settle(db, sip({"ABCD": flat}), cost, now=now) == ["ABCD L4b void"]
    (t,) = trades(db)
    assert t["status"] == "seen" and t["net"] is None and t["reason"] == lb.NO_FILL and t["settled_at"] is not None
    assert lb.settle(db, sip({"ABCD": flat}), cost, now=now) == []     # not retried


def test_a_long_whose_target_cannot_beat_costs_is_not_taken(db, monkeypatch):
    """Owner 2026-11-17 (decision 4; MPU: buy 1.51, stop 1.50, target 1.53 at 1.3% costs)."""
    ts = pd.Timestamp("2026-11-09 15:03", tz="UTC")
    tight = pt.Setup("L4a", "long", ts, None, 9.99)
    wide = pt.Setup("L4a", "long", ts, None, 9.50)
    trade = pt.Trade("L4a", "long", ts, 10.0, ts, 10.0, "time", 0.0)
    c = est_costs.cost("long", "ABCD", ts.to_pydatetime(), 10.0)
    assert 0.002 < c < 0.05
    assert lb.no_win("ABCD", tight, trade) is True                                    # 2R = 0.2% against the cost
    assert lb.no_win("ABCD", wide, trade) is False                                    # 2R = 10%
    edge = pt.Setup("L4a", "long", ts, None, 10.0 * (1 - 0.999 * c / 2))              # 2R just under the cost: cannot win
    assert lb.no_win("ABCD", edge, trade) is True
    # on DAY the red-candle break (L4b) fills first; when it cannot win the book takes the red-candle close instead
    alert_row(db)
    monkeypatch.setattr(lb, "no_win", lambda sym, s, t: s.kind == "L4b")
    sent = []
    assert lb.tick(db, feed(DAY), sent.append, now=at(3)) == 1
    (t,) = trades(db)
    assert (t["kind"], t["est_entry"]) == ("L4a", 10.3) and "red-candle close" in sent[0]
    assert t["status"] == "est_closed" and t["reason"] == "target"                    # its 10.50 target traded that bar


def test_only_no_win_fills_leave_a_marker_and_no_text(db, monkeypatch):
    alert_row(db)
    monkeypatch.setattr(lb, "no_win", lambda sym, s, t: True)
    sent = []
    assert lb.tick(db, feed(DAY), sent.append, now=at(3)) == 0 and trades(db) == []   # still inside its window
    late = (POSTED + pd.Timedelta(fade.MAX_ENTRY_DELAY) + ls.REOPEN_MAX + pt.WATCH + lb.GRACE).to_pydatetime()
    assert lb.tick(db, feed(DAY), sent.append, now=late) == 0
    (t,) = trades(db)
    assert t["status"] == "nowin" and t["kind"] in ("L4a", "L4b") and sent == []
    assert lb.today_line(db, "2026-11-09") is None and lb.seen_line(db, "2026-11-09") is None


def test_an_open_long_still_exits_after_the_switch(db):
    alert_row(db)
    sent = []
    lb.tick(db, feed(DAY), sent.append, now=at(3))
    assert [t["status"] for t in trades(db)] == ["open"]
    db.set_flag(lb.OFF_FLAG, "1")
    lb.tick(db, feed(DAY), sent.append, now=at(50))
    assert [t["status"] for t in trades(db)] == ["est_closed"] and len(sent) == 2


def test_digest_says_longs_are_off(db):
    from zoneinfo import ZoneInfo

    from sgt.ops import digest
    db.set_flag(lb.OFF_FLAG, "1")
    text = digest.build(db, datetime(2026, 11, 9, 20, 50, tzinfo=ZoneInfo("America/New_York")), fills=[],
                        problems=[], broker=None, research=[])
    assert "(your choice): each setup is texted as seen, none is traded or counted" in text
    assert "measure only): the long shadow" not in text                             # the 11-13 wording is gone


def settled(db, n, net, start=1000):
    with db.tx() as c:
        for i in range(n):
            c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, weight, net, created_at) VALUES "
                      "(?,?,?,?,?,?,?,?)", (start + i, f"S{i}", "2026-11-09", "L4b", "settled", 0.005, net(i), "x"))


def test_an_unproven_book_trades_at_test_size_and_a_proven_one_at_full_size(db):
    """2026-11-19: the book wins 15.6% of its trades and needs 32.6%. Until it meets the owner's proof standard
    (300 settled live trades, interval of the mean above zero) a long puts at most PROBE_USD to work; every long
    still trades and is texted. The risk weight and the drift scale apply inside the cap as before."""
    s = pt.Setup("L4b", "long", T1, 10.55, 10.2)
    t = pt.Trade("L4b", "long", T1, 10.55, T1, 10.55, "time", 0.0)
    tight = pt.Setup("L4b", "long", T1, 10.0, 9.9)                                       # GP's shape: a 1% stop
    tt = pt.Trade("L4b", "long", T1, 10.0, T1, 10.0, "time", 0.0)
    v = lb.nav(db)
    assert not lb.proven(db) and lb.probe_usd(db) == 500.0
    assert lb.weight(db, tight, tt, v) * v == pytest.approx(500.0)                       # was $5,000 on the tightest stops
    assert lb.weight(db, s, t, v) * v == pytest.approx(500.0 * lb.risk_weight(10.55, 10.2))
    assert lb.full_weight(db, tight, tt, v) * v == pytest.approx(5_000.0)
    assert "$500 (paper, test size; full size $5,000)\n" in lb.entry_text(db, "ABCD", tight, tt)
    assert "test size" not in (lb.record_line(db) or "")                                # no trades yet: no line
    for flag, want in (("250", 250.0), ("20000", 5_000.0), ("abc", 500.0), ("-5", 500.0), ("0", 500.0), ("nan", 500.0), ("", 500.0)):
        db.set_flag(lb.PROBE_FLAG, flag)                                                 # the owner's amount; junk never raises size
        assert lb.weight(db, tight, tt, v) * v == pytest.approx(want), flag
    db.set_flag(lb.PROBE_FLAG, "")
    settled(db, 299, lambda i: 0.02 if i % 3 else -0.01)
    assert not lb.proven(db)                                                             # winning, but 299 trades
    # 2026-11-24: the size rule and the registered paper review are two rules, and the line names both
    assert ("; test size $500 a long (size rule: until 300 settled live trades with a 95% interval above zero; the "
            "registered paper review is separate: early look at 15, review at 30 trades with no exclusion flag from "
            "11-23)") in lb.record_line(db)
    settled(db, 1, lambda i: 0.02, start=5000)
    assert lb.proven(db) and lb.probe_usd(db) is None                                    # 300 trades, interval above zero
    v = lb.nav(db)
    assert lb.weight(db, tight, tt, v) == pytest.approx(lb.full_weight(db, tight, tt, v)) and lb.weight(db, tight, tt, v) * v > 4_000
    assert "(paper)\n" in lb.entry_text(db, "ABCD", tight, tt) and "test size" not in lb.record_line(db)


def test_three_hundred_losing_or_noisy_trades_prove_nothing(db):
    settled(db, 300, lambda i: -0.02 if i % 6 else 0.08)                                 # the book's real shape: mean below zero
    assert not lb.proven(db) and lb.probe_usd(db) == 500.0
    with db.tx() as c:
        c.execute("DELETE FROM long_trades")
    settled(db, 300, lambda i: 0.30 if i % 50 == 0 else -0.005)                          # mean just above zero, interval not
    assert not lb.proven(db)


def test_the_setup_as_the_book_saw_it_is_kept_on_the_row_and_the_decision_line():
    """BNAI 2026-11-25: to learn which base the book had bought, the free feed had to be fetched again. The detector's
    own record of the setup (base high and low, break candle, VWAP) and its width now stay with the trade."""
    import json

    s = pt.Setup("L8", "long", T1, None, 7.89, {"base_high": 8.18, "base_low": 7.89, "break_ts": T1 - pt.BAR, "vwap": 8.1741})
    seen = json.loads(lb.setup_seen(s))
    assert seen == {"base_high": 8.18, "base_low": 7.89, "break_ts": (T1 - pt.BAR).isoformat(), "vwap": 8.1741,
                    "base_width": round((8.18 - 7.89) / 7.89, 6), "feed": "free feed (the live candles)"}
    assert lb.setup_seen(None) is None and lb.setup_seen(pt.Setup("L4b", "long", T1, 1.1, 1.0)) is None

