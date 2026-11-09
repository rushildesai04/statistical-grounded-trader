"""Live alert-fade book (src/sgt/fast/fade.py, scripts/fade_live.py): gates, estimates, official settlement."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import re

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_live as fl  # noqa: E402
import telegram_alert_test as tat  # noqa: E402
from alert_fade_test import LOCATE  # noqa: E402
from sgt.fast import fade  # noqa: E402
from sgt.ops.sms_style import bold, italic  # noqa: E402
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


@pytest.fixture
def ask_first(monkeypatch):
    """Legacy F3/F4 ask-first approvals (kept behind fade.ASK_FIRST since amendment F5)."""
    monkeypatch.setattr(fade, "ASK_FIRST", True)


ALERT_AT = T10 - timedelta(seconds=10)          # alert + 10 s = 10:00:00, the start of the tested entry bar


def alert(sym="ABC", typ="volume_spike", at=ALERT_AT, mid=1, news=None):
    return fade.LiveAlert(sym, typ, at, "-1", mid, news)


def rows(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM fade_trades ORDER BY id")]


def test_cells_match_the_research_definition():
    assert fade.CELLS == {k for k, v in tat.CELL_OF.items() if v}
    assert (fade.REACTION, fade.MAX_ENTRY_DELAY) == (tat.REACTION.to_pytimedelta(), tat.MAX_ENTRY_DELAY.to_pytimedelta())


def test_unarmed_event_asks_and_blocks_later_alerts_window_filter_does_not(db, ask_first):
    early = alert(at=datetime(2026, 11, 2, 9, 0, tzinfo=ET).astimezone(timezone.utc))
    assert fade.on_alert(db, early, now=early.posted_at) == "filtered: window"
    assert fade.on_alert(db, alert(), now=T10) == "ask #2"
    ap.arm(db, T10)
    assert fade.on_alert(db, alert(mid=2), now=T10) == "duplicate symbol-session"
    assert fade.on_alert(db, alert("XYZ", "halted"), now=T10) == "not a cell"
    assert fade.on_alert(db, alert("NWS", "news", news=False), now=T10) == "not a cell"
    db.set_flag("paused", "1")
    assert fade.on_alert(db, alert("PSD", mid=5), now=T10) == "skipped: paused"
    assert [r["status"] for r in rows(db)] == ["filtered", "ask", "skipped"]


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
    assert out[0].startswith(f"[SGT] ✅ {bold('FADE IN')} · {bold('SHORT UP')} @ {bold('$10.00')}\n"
                             "💬 Shorted $5,000 of UP: we make money if it falls")             # 5% of $100k
    assert "🛑 stop $12.00" in out[0] and "⏰ out 11:00 ET" in out[0]
    px.update(UP=12.5, DN=9.0)
    fade.monitor(db, out, px.get, now=T10 + timedelta(minutes=30))
    up, dn = rows(db)[:2]
    assert up["status"] == "est_closed" and up["est_exit"] == 12.5 and up["reason"] == "stop"
    assert dn["status"] == "open" and dn["est_last"] == 9.0
    fade.monitor(db, out, lambda s: None, now=T10 + timedelta(minutes=61))   # no quote: last mark is used
    dn = rows(db)[1]
    assert dn["status"] == "est_closed" and dn["est_exit"] == 9.0 and dn["reason"] == "60 min"
    assert out[-1] == (f"[SGT] 🟢 {bold('FADE OUT')} · {bold('DN')} · made {bold('+$500 (+10.0%)')}\n"
                       "Shorted $10.00 → bought back $9.00 · ⏰ 60 minutes up\n"
                       "💡 Why: price fell 10.0% while we were short\n"
                       f"📅 Today: 2 trades, {bold('-$750')}\n"
                       "📈 https://www.tradingview.com/chart/?symbol=DN\n"
                       + italic("Estimate before costs; official numbers in the midnight summary."))
    assert out[-2].startswith(f"[SGT] 🛑 {bold('FADE OUT')} · {bold('UP')} · lost {bold('-$1,250 (-25.0%)')}\n"
                              "Shorted $10.00 → bought back $12.50 · 🛑 stop hit")
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
            {"id": ids["W"], "qualifies": True, "entry_ts": "t", "entry": 10, "cover": 9, "cost_rt": 0.01, "gross": 0.1, "net": 0.085,
             "rule_entry": 10.2, "rule_net": 0.1},
            {"id": ids["L"], "qualifies": True, "entry_ts": "t", "entry": 10, "cover": 12, "cost_rt": 0.01, "gross": -0.2, "net": -0.215},
            {"id": ids["V"], "qualifies": False, "why": "outside research filters", "net": None}])  # LATE: no bars yet
    assert fade.settle(db, official, now=T10 + timedelta(minutes=10)) == []        # not due before 80 min
    got = fade.settle(db, official, now=T10 + timedelta(hours=2))
    assert got == ["W +8.50%", "L -21.50%", "V void"]
    assert {r["symbol"]: r["status"] for r in rows(db)} == {"W": "settled", "L": "settled", "V": "void", "LATE": "open"}
    assert fade.nav(db) == pytest.approx(fade.NAV0 * (1 + 0.05 * 0.085) * (1 - 0.05 * 0.215))
    w = next(r for r in rows(db) if r["symbol"] == "W")
    assert (w["rule_entry"], w["rule_net"]) == (10.2, 0.1)
    assert next(r for r in rows(db) if r["symbol"] == "L")["rule_net"] is None   # older scorer output: left empty
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


def code_in(text: str) -> str:
    return re.search(r"OK (\d{6}|\d{2})\b", text).group(1)


def test_per_trade_approval_enters_and_is_scored_from_the_real_entry_time(db, ask_first):
    # amendment F3: not ARMed -> one approval text per event; OK -> entry at the next quote
    out = Outbox()
    assert fade.on_alert(db, alert("APR"), now=T10) == "ask #1"
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=15))
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=25))      # no duplicate request
    assert len(out) == 1 and out[0].startswith(f"[SGT] 🔻 {bold('SHORT APR')} @ {bold('$10.00')}") and "👉 NO " in out[0]
    msg, row = ap.answer(db, ap.Command("OK", code_in(out[0])), "SM1", now=T10 + timedelta(minutes=2))
    assert row["status"] == "approved"
    fade.monitor(db, out, lambda s: 10.5, now=T10 + timedelta(minutes=2, seconds=10))
    r = rows(db)[0]
    assert r["status"] == "open" and r["reason"] == "approved" and r["est_entry"] == 10.5
    assert out[-1].startswith(f"[SGT] ✅ {bold('FADE IN')} · {bold('SHORT APR')} @ {bold('$10.50')}")
    assert ap.get(db, row["id"])["status"] == "executed"
    fade.monitor(db, out, lambda s: 9.0, now=T10 + timedelta(minutes=63))
    d = fade.due(db, T10 + timedelta(hours=3))
    assert d.entry_after.iloc[0] == r["est_entry_at"]                          # official entry = real entry


def test_declined_skips_and_silence_auto_enters_at_the_deadline(db, ask_first):
    # owner decision 2026-11-04: opt-out; silence at the deadline enters (inside the tested 5-minute window)
    out = Outbox()
    fade.on_alert(db, alert("NO1"), now=T10)
    fade.on_alert(db, alert("LATE", mid=2), now=T10)
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=15))
    no1 = next(t for t in out if bold("NO1") in t)
    ap.answer(db, ap.Command("NO", code_in(no1)), "SM2", now=T10 + timedelta(minutes=1))
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(minutes=4, seconds=45))   # deadline + margin: 4:50
    got = {r["symbol"]: (r["status"], r["reason"]) for r in rows(db)}
    assert got == {"NO1": ("skipped", "declined"), "LATE": ("open", "auto")}
    late_code = code_in(next(t for t in out if bold("LATE") in t))
    reply, row = ap.answer(db, ap.Command("NO", late_code), "SM3", now=T10 + timedelta(minutes=6))
    assert row is None and "already entered" in reply and db.count_events("bad_code", T10) == 0
    assert fade.due(db, T10 + timedelta(hours=3)).set_index("symbol").entry_after.notna()["LATE"]


def test_approval_request_is_not_sent_after_the_deadline_or_outside_price(db, ask_first):
    out = Outbox()
    fade.on_alert(db, alert("SLOW"), now=T10)
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(minutes=4, seconds=30))
    t2 = T10 + timedelta(minutes=10)
    fade.on_alert(db, alert("BIG", at=t2, mid=2), now=t2)
    fade.monitor(db, out, lambda s: 80.0, now=t2 + timedelta(seconds=15))
    assert out == []
    assert [(r["status"], r["reason"]) for r in rows(db)] == [("skipped", "no time left to ask"),
                                                              ("filtered", "price 80.00 outside $1-50")]


def test_official_shifts_approved_trades_to_their_entry_time(tmp_path, monkeypatch):
    store = ParquetBarStore(tmp_path / "bars")
    write_bars(store, "APR", {m: (9.0, 9.0) for m in range(34, 120)})       # drops to 9.00 from 10:04
    monkeypatch.setattr(fl, "BARS", tmp_path / "bars")
    monkeypatch.setattr(fl, "backfill", lambda *a, **k: {})
    base = {"id": 1, "symbol": "APR", "alert_type": "volume_spike", "posted_at": T10.isoformat(), "session": "2026-11-02"}
    auto = fl.official(pd.DataFrame([{**base, "entry_after": None}]), now=T10 + timedelta(hours=10)).iloc[0]
    late = fl.official(pd.DataFrame([{**base, "entry_after": (T10 + timedelta(minutes=4, seconds=5)).isoformat()}]),
                       now=T10 + timedelta(hours=10)).iloc[0]
    assert auto.entry == pytest.approx(10.0) and late.entry == pytest.approx(9.0)
    # plan W5: the delayed trade is also scored at the tested timing, so the cost of waiting is measured
    assert auto.rule_entry == pytest.approx(10.0) and auto.rule_net == pytest.approx(auto.net)
    assert late.rule_entry == pytest.approx(10.0) and late.rule_net == pytest.approx(auto.net)
    assert late.net - late.rule_net == pytest.approx(-0.10, abs=0.005)      # entered after the 10% drop


def test_late_entry_exits_at_the_close_like_the_research_window(db):
    ap.arm(db, T10)
    t = datetime(2026, 11, 2, 15, 30, tzinfo=ET).astimezone(timezone.utc)
    fade.on_alert(db, alert("EOD", at=t - timedelta(seconds=10)), now=t)
    out = Outbox()
    fade.monitor(db, out, lambda s: 10.0, now=t + timedelta(seconds=20))
    assert "out 16:00 ET" in out[-1]
    fade.monitor(db, out, lambda s: 9.5, now=t + timedelta(minutes=29))
    assert rows(db)[0]["status"] == "open"
    fade.monitor(db, out, lambda s: 9.5, now=t + timedelta(minutes=30, seconds=5))
    r = rows(db)[0]
    assert r["status"] == "est_closed" and r["reason"] == "close" and "🔔 market close" in out[-1]
    friday = datetime(2026, 11, 27, 12, 30, tzinfo=ET)                     # early close 13:00 on Black Friday
    assert fade._exit_at("2026-11-27", friday) == (datetime(2026, 11, 27, 13, 0, tzinfo=ET), "close")


def test_live_client_source_reads_oldest_first_from_the_listener_connection():
    # plan 2026-11-04 W2: the research record is filled over the listener's own client (no second login)
    import asyncio
    from types import SimpleNamespace

    msgs = [SimpleNamespace(id=7, date=T10, message="$ABC volume spike", edit_date=None), None]
    seen = {}

    class Client:
        async def iter_messages(self, entity, **kw):
            seen.update(entity=entity, **kw)
            for m in msgs:
                yield m

    async def collect():
        src = fl.LiveClientSource(Client())
        return [m async for m in src.iter_messages("-1001254273411", min_id=0, limit=50, since=T10)]
    got = asyncio.run(collect())
    assert [m.id for m in got] == [7] and got[0].text == "$ABC volume spike" and got[0].edited_at is None
    assert seen == {"entity": -1001254273411, "reverse": True, "limit": 50, "min_id": 0, "offset_date": T10}


def test_disarm_and_pause_block_auto_entry(db, ask_first):
    out = Outbox()
    fade.on_alert(db, alert("DIS"), now=T10)
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=15))
    ap.disarm(db, now=T10 + timedelta(minutes=1))
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(minutes=4, seconds=30))
    assert rows(db)[0]["status"] == "skipped" and "disarmed" in rows(db)[0]["reason"]
    ap.arm(db, T10 + timedelta(minutes=5))                     # ARM later the same day re-enables
    assert not ap.disarmed(db, T10 + timedelta(minutes=5))


def test_request_expired_by_pause_never_auto_enters_after_resume(db, ask_first):
    out = Outbox()
    fade.on_alert(db, alert("PZ"), now=T10)
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=15))
    ap.pause(db, T10 + timedelta(minutes=1))
    db.set_flag("paused", "0")                                   # RESUME seconds later
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(minutes=4, seconds=30))
    assert rows(db)[0]["status"] == "skipped"


def test_approval_text_has_confidence_reason_tape_risk_payoff_record_and_borrow():
    # owner requests 2026-11-04: everything needed to decide; bold/italic/emoji for a phone; confidence score
    # 2026-11-05: confidence moves with the one tested input that matters (company news, prereg_fade_news A1)
    earn = {"news_any": True, "news_own": True, "news_earn": True, "news_n": 1, "headline": "XYZ raises guidance"}
    body = fade.ask_text("XYZ", "resumed", 4.2, 5000.0,
                         {"chg_day": 0.85, "chg_open": 0.12, "off_high": -0.09, "vwap": 3.78, "rvol": 3.24,
                          "news": earn},
                         fade.borrow_note({"ib_avail": 75000, "ib_fee": 44.2}), (21, -0.0198))
    assert body.split("\n") == [
        f"🔻 {bold('SHORT XYZ')} @ {bold('$4.20')}",
        "💬 Short $5,000 of XYZ: we make money if it falls",
        f"🎲 Win chance {bold('52%')} · earnings news today: {italic('avg +0.1%/trade; 55% sure that average is positive')}",
        "📰 “XYZ raises guidance”",
        "📣 halt resumed · up 85% today · +12% from open",
        f"📊 9% off high · +11% vs VWAP $3.78 · vol 3.2x yday {italic('(IEX)')}",
        "💵 $5,000 · 🛑 stop $5.04 ≈ -$1,120 · ⏳ 60 min",
        f"🎯 Tested (764 unseen): win 57%, avg {bold('+$56')}",
        "    wins +$670 · losses -$765 · 16% stopped",
        f"📈 Live: 21 trades, avg -2.0% {italic('(too few to judge)')}",
        "🏦 IBKR 75k sh @ 44%/yr",
        "🔗 https://www.tradingview.com/chart/?symbol=XYZ"]
    quiet = {"news_any": False, "news_own": False, "news_earn": False, "news_n": 0, "headline": None}
    clean = fade.ask_text("XYZ", "resumed", 4.2, 5000.0, {"news": quiet}, "")
    assert f"🎲 Win chance {bold('58%')} · no company news" in clean and "📰" not in clean
    bare = fade.ask_text("XYZ", "news", None, 5000.0, {}, "")
    assert "📣 news\n" in bare and "🏦 borrow unknown" in bare and "stop +20%" in bare
    assert "📊" not in bare and "📈" not in bare and "@ market" in bare
    none = fade.ask_text("XYZ", "news", 4.2, 5000.0, {}, fade.borrow_note({"ib_avail": 0}))
    assert "\n🏦 ⚠️ none at IBKR (real short unlikely)\n" in none
    assert "too few" not in fade.ask_text("XYZ", "news", 4.2, 5000.0, {}, "", (300, 0.01))


def test_record_counts_only_settled_trades(db):
    assert fade.record(db) == (0, None)
    with db.tx() as c:
        for i, (st, net) in enumerate([("settled", 0.10), ("settled", -0.04), ("est_closed", None)]):
            c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "net, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (f"S{i}", "2026-11-04", "news", "x", "c", i, st,
                                                                     net, "x"))
    assert fade.record(db) == (2, pytest.approx(0.03))


def test_iex_context_computes_moves_and_tolerates_bad_payloads(monkeypatch):
    monkeypatch.setattr(fl.social, "lookup", lambda symbol, at: None)          # no network in tests
    monkeypatch.setattr(fl.xsocial, "lookup", lambda symbol, at: None)
    monkeypatch.setattr(fl.reddit, "lookup", lambda symbol, at: None)
    import httpx

    def handler(req):
        if req.url.path == "/v1beta1/news":                     # Benzinga feed: untrusted text, one bad item
            if req.url.params["symbols"] == "BAD":
                return httpx.Response(500)
            created = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
            return httpx.Response(200, json={"news": [
                {"created_at": created, "symbols": ["XYZ"], "headline": "XYZ Q3 EPS beats\nraises guidance"},
                "junk"]})
        if "BAD" in req.url.path:
            return httpx.Response(200, json=["not", "a", "dict"])
        return httpx.Response(200, json={"latestTrade": {"p": 3.7}, "dailyBar": {"o": 3.3, "h": 4.0, "vw": 3.5, "v": 9e5},
                                         "prevDailyBar": {"c": 2.0, "v": 3e5}})
    ctx = fl.iex_context(httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)))
    got = ctx("XYZ")
    assert got["chg_day"] == pytest.approx(0.85) and got["chg_open"] == pytest.approx(3.7 / 3.3 - 1)
    assert got["off_high"] == pytest.approx(3.7 / 4.0 - 1) and got["vwap"] == 3.5 and got["rvol"] == pytest.approx(3.0)
    assert got["news"]["news_earn"] and got["news"]["headline"] == "XYZ Q3 EPS beats raises guidance"
    assert fade.news_group(got) == "earn"
    assert ctx("BAD") == {"news": None, "filings": None, "social": None, "x": None, "reddit": None} and ctx("not a symbol") == {}   # unknown, never "none"
    assert fade.news_group(ctx("BAD")) == "all"


def exit_code(text: str) -> str:
    return re.search(r"NO (\d{2}) = exit now", text).group(1)


def test_unarmed_alert_enters_at_the_tested_time_and_texts_the_trade_with_an_exit_code(db):
    # amendment F5 (owner decision 2026-11-04): no waiting; the entry text carries the card and NO = exit now
    out = Outbox()
    assert fade.on_alert(db, alert("VTO"), now=T10) == "pending #1"
    fade.monitor(db, out, lambda s: 10.0, now=T10 - timedelta(seconds=1))
    assert out == [] and rows(db)[0]["status"] == "pending"               # not before the tested entry bar
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=12),
                 context=lambda s: {"chg_day": 0.5})
    r = rows(db)[0]
    assert r["status"] == "open" and r["reason"] == fade.VETO and r["est_entry"] == 10.0
    assert len(out) == 1 and out[0].startswith(f"[SGT] ✅ {bold('FADE IN')} · {bold('SHORT VTO')} @ {bold('$10.00')}")
    assert "up 50% today" in out[0] and "⏰ out 11:00" in out[0] and out[0].endswith(f"👉 NO {exit_code(out[0])} = exit now")
    assert fade.due(db, T10 + timedelta(hours=3)).entry_after.isna().all()   # scored at the tested timing


def test_no_exits_at_the_next_quote_and_late_codes_say_closed(db):
    out = Outbox()
    for i, s in enumerate(["NOX", "KEEP"]):
        fade.on_alert(db, alert(s, mid=i), now=T10)
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=12))
    nox, keep = (exit_code(next(t for t in out if bold(s) in t)) for s in ("NOX", "KEEP"))
    ap.pause(db, T10 + timedelta(minutes=5))                               # PAUSE keeps exit offers open
    db.set_flag("paused", "0")
    reply, _ = ap.answer(db, ap.Command("NO", nox), "SM1", now=T10 + timedelta(minutes=10))
    assert reply == "EXIT: closing NOX at the next price (about 10 s)."
    assert ap.answer(db, ap.Command("OK", keep), "SM2", now=T10 + timedelta(minutes=10))[0].startswith("KEEP: KEEP")
    fade.monitor(db, out, lambda s: 9.5, now=T10 + timedelta(minutes=10, seconds=10))
    got = {r["symbol"]: r for r in rows(db)}
    assert got["NOX"]["status"] == "est_closed" and got["NOX"]["reason"] == fade.VETO_EXIT
    assert got["KEEP"]["status"] == "open"
    assert out[-1].startswith(f"[SGT] 🟢 {bold('FADE OUT')} · {bold('NOX')} · made")
    assert "bought back $9.50 · 👋 you texted NO" in out[-1]
    fade.monitor(db, out, lambda s: 9.5, now=T10 + timedelta(minutes=61))
    assert {r["symbol"]: r["reason"] for r in rows(db)}["KEEP"] == "60 min"
    assert ap.answer(db, ap.Command("NO", keep), "SM3", now=T10 + timedelta(minutes=62))[0] == \
        "No change: KEEP already closed."
    assert db.count_events("bad_code", T10) == 0


def test_disarm_or_pause_before_entry_blocks_an_f5_trade(db):
    out = Outbox()
    fade.on_alert(db, alert("DIS"), now=T10)
    ap.disarm(db, "test", T10 + timedelta(seconds=2))
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=12))
    assert rows(db)[0]["status"] == "skipped" and out == []
    ap.pause(db, T10)
    assert fade.on_alert(db, alert("PSD", mid=2), now=T10) == "skipped: paused"


def test_official_covers_a_no_exit_at_the_last_close_before_it(tmp_path, monkeypatch):
    store = ParquetBarStore(tmp_path / "bars")
    write_bars(store, "NOX", {m: (9.0, 9.0) for m in range(35, 120)})       # 9.00 from 10:05
    monkeypatch.setattr(fl, "BARS", tmp_path / "bars")
    monkeypatch.setattr(fl, "backfill", lambda *a, **k: {})
    base = {"id": 1, "symbol": "NOX", "alert_type": "volume_spike", "posted_at": T10.isoformat(),
            "session": "2026-11-02", "entry_after": None}
    no = {**base, "reason": fade.VETO_EXIT, "est_exit_at": (T10 + timedelta(minutes=3, seconds=30)).isoformat()}
    full = fl.official(pd.DataFrame([{**base, "reason": "60 min", "est_exit_at": None}]),
                       now=T10 + timedelta(hours=10)).iloc[0]
    cut = fl.official(pd.DataFrame([no]), now=T10 + timedelta(hours=10)).iloc[0]
    assert full.gross == pytest.approx(0.10) and cut.cover == pytest.approx(10.0) and cut.gross == pytest.approx(0.0)
    assert cut.rule_net == pytest.approx(full.net) and cut.net - cut.rule_net == pytest.approx(-0.10)


def test_live_bars_feed_the_chart_read_and_tolerate_junk():
    import httpx

    t0 = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)
    good = [{"t": (t0 + timedelta(minutes=i)).isoformat(), "o": 3 + i / 100, "h": 3.02 + i / 100, "l": 2.99 + i / 100,
             "c": 3.01 + i / 100, "v": 1000 + i} for i in range(40)] + [{"t": "x", "o": "bad"}, "junk"]

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/bars"):
            return httpx.Response(200, json={"bars": good} if "XYZ" in req.url.path else ["no"])
        return httpx.Response(500)

    c = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    b = fl.live_bars(c, "XYZ", t0 + timedelta(minutes=45))
    assert len(b) == 40 and b.ts.is_monotonic_increasing
    assert fl.live_bars(c, "BAD", t0).empty
    line = fade.signals_line({"rsi14": 91.2, "ema_cross_state": 1.0, "ema9_21_gap_pct": 0.021, "macd_hist_pct": -0.001,
                              "psar_side": 1.0, "pvt_trend": -1.0, "off_hod_pct": 0.0})
    assert line.startswith("📈 RSI 91 · EMA9 above 21 (+2.1%) · MACD fading · 4/6 reads favor the short")
    assert fade.signals_line(None) is None and fade.signals_line({}) is None


def test_time_of_day_history_line_by_entry_window():
    ctx = {"news": {"news_any": 0, "news_own": 0, "news_earn": 0}}
    t = fade.ask_text("ABCD", "volume_spike", 4.0, 5000, ctx, "", at=datetime(2026, 11, 5, 15, 30, tzinfo=timezone.utc))
    assert f"🎲 Win chance {bold('57%')} · no company news, before 11:00 ET" in t  # v2 (A2)
    t = fade.ask_text("ABCD", "volume_spike", 4.0, 5000, ctx, "", at=datetime(2026, 11, 5, 19, 30, tzinfo=timezone.utc))
    assert f"🎲 Win chance {bold('65%')} · no company news, after 14:00 ET" in t
    raya = {"news": {"news_any": 1, "news_own": 1, "news_earn": 0}}                  # RAYA / STAK / SERV 2026-11-06
    t = fade.ask_text("RAYA", "news", 1.52, 5000, raya, "", at=datetime(2026, 11, 6, 14, 39, tzinfo=timezone.utc))
    assert f"🎲 Win chance {bold('53%')} · company news today, before 11:00 ET" in t
    assert fade.confidence("news", {}, datetime(2026, 11, 6, 19, 0, tzinfo=timezone.utc))[0] == 66    # news alert, 14:00
    assert fade.confidence("volume_spike", {}, datetime(2026, 11, 6, 15, 0, tzinfo=timezone.utc))[0] == 93   # unknown


def test_stale_print_is_no_price_only_when_it_looks_halted():
    import httpx

    # RAYA 2026-11-06: alert during a LULD halt after +31% in 5 minutes -> stale print is not a price.
    # SNT 2026-11-06: quiet on IEX but trading on SIP, flat -> the stale print is still the estimate.
    now = datetime(2026, 11, 6, 14, 39, 54, tzinfo=timezone.utc)
    last = {"RAYA": "2026-11-06T14:37:41.123456789Z", "SNT": "2026-11-06T14:37:02Z", "NOBARS": "2026-11-06T14:37:00Z",
            "LIVE": "2026-11-06T14:39:50.5Z", "BAD": "not-a-time"}
    t0 = datetime(2026, 11, 6, 14, 32, tzinfo=timezone.utc)

    def bars(path):
        out = []
        for i, px in enumerate(path):
            out.append({"t": (t0 + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"), "o": px, "h": px * 1.01,
                        "l": px * 0.99, "c": px, "v": 1000})
        return out

    series = {"RAYA": bars([1.16, 1.18, 1.24, 1.32, 1.52, 1.52]), "SNT": bars([1.90, 1.91, 1.89, 1.90, 1.88, 1.90]),
              "NOBARS": []}

    def handler(req):
        sym = req.url.path.split("/")[3]
        if req.url.path.endswith("/bars"):
            return httpx.Response(200, json={"bars": series.get(sym, [])})
        return httpx.Response(200, json={"trade": {"p": 1.425, "t": last[sym]}})

    q = fl.iex_quote(httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)), now=lambda: now)
    assert q("RAYA") is None                    # stale after a violent move: likely halted, wait
    assert q("SNT") == 1.425                    # stale but flat: thin on IEX, keep the estimate
    assert q("NOBARS") is None                  # unknown: the safe side is to wait
    assert q("LIVE") == 1.425 and q("BAD") is None
    assert q.since_aware and q("LIVE", since=pd.Timestamp(last["LIVE"]).to_pydatetime()) == 1.425   # print at since
    assert q("LIVE", since=pd.Timestamp(last["LIVE"]).to_pydatetime() + timedelta(seconds=1)) is None   # older: wait


def test_digest_warns_when_the_channel_goes_quiet(db):
    with db.tx() as c:
        for i, d in enumerate(["2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"]):
            for k in range(12):
                c.execute("INSERT INTO fade_trades (symbol, session, alert_type, posted_at, channel, message_id, "
                          "status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                          (f"S{i}{k}", d, "news", f"{d}T15:00:00+00:00", "-1", i * 100 + k, "filtered", "x",
                           f"{d}T15:00:00+00:00"))
    assert fade.channel_health(db, DAY.isoformat()) == ["! channel quiet: 0 alerts today vs usual 12 (listener or "
                                                       "channel change?)"]
    assert any("channel quiet" in x for x in fade.digest_lines(db, DAY, T10))
    assert fade.channel_health(db, "2026-10-26") == []          # fewer than 5 past sessions: no judgement


def test_entry_waits_for_the_tested_bar_and_a_halt_after_the_alert_is_no_trade(db):
    # OMH 2026-11-09: alert 09:38:18, last print 09:38:33 at $27.04, halted 09:39-09:47, reopened above the stop.
    # The tested rule enters at the open of the first bar at or after alert + 10 s (09:39), within 5 minutes.
    assert fade.entry_start(datetime(2026, 11, 9, 14, 38, 18, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 9, 14, 39, tzinfo=timezone.utc)
    assert fade.entry_start(ALERT_AT) == T10                             # alert + 10 s exactly on a bar start
    ap.arm(db, T10)
    posted = T10 + timedelta(seconds=18)                                # 10:00:18 -> tested bar 10:01
    fade.on_alert(db, alert("OMH", at=posted), now=posted)
    prints = {"t": posted + timedelta(seconds=15), "p": 27.04}         # last print before a halt

    def quote(symbol, since=None):
        return None if since is not None and prints["t"] < since else prints["p"]
    quote.since_aware = True
    out = Outbox()
    fade.monitor(db, out, quote, now=posted + timedelta(seconds=20))    # mid-minute: the old book shorted here
    assert rows(db)[0]["status"] == "pending" and out == []
    fade.monitor(db, out, quote, now=T10 + timedelta(minutes=1, seconds=5))   # bar 10:01 started, no new print
    assert rows(db)[0]["status"] == "pending"
    fade.monitor(db, out, quote, now=posted + timedelta(minutes=5, seconds=5))   # still halted past 5 minutes
    r = rows(db)[0]
    assert r["status"] == "skipped" and r["reason"].startswith("no trade print within 5 min")
    assert out == []


def test_entry_uses_the_first_print_inside_the_tested_bar(db):
    ap.arm(db, T10)
    posted = T10 + timedelta(seconds=18)
    fade.on_alert(db, alert("GO", at=posted), now=posted)
    prints = {"t": posted, "p": 5.0}

    def quote(symbol, since=None):
        return None if since is not None and prints["t"] < since else prints["p"]
    quote.since_aware = True
    out = Outbox()
    prints.update(t=T10 + timedelta(minutes=1, seconds=2), p=5.2)      # trading continues into the 10:01 bar
    fade.monitor(db, out, quote, now=T10 + timedelta(minutes=1, seconds=5))
    r = rows(db)[0]
    assert r["status"] == "open" and r["est_entry"] == 5.2


def test_entry_window_uses_the_tested_bar_minute(db):
    et = lambda h, m, s: datetime(2026, 11, 2, h, m, s, tzinfo=ET).astimezone(timezone.utc)  # noqa: E731
    assert fade.on_alert(db, alert("EARLY", at=et(9, 29, 45)), now=et(9, 29, 46)) != "filtered: window"   # bar 09:30
    assert fade.on_alert(db, alert("LATE1", at=et(15, 43, 50), mid=2), now=et(15, 43, 51)) != "filtered: window"
    assert fade.on_alert(db, alert("LATE2", at=et(15, 44, 30), mid=3), now=et(15, 44, 31)) == "filtered: window"
