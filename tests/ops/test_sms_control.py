"""SMS control plane (scripts/sms_control.py) and the 60/40 approval gate (scripts/rebalance_passive.py)."""
from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import rebalance_passive as rp  # noqa: E402
import sms_control as sc  # noqa: E402
from sgt.allocation.rebalance import PlannedOrder  # noqa: E402
from sgt.ops import approvals as ap  # noqa: E402
from sgt.ops.sms import Inbound  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402


class FakeSms:
    def __init__(self, inbound=()):
        self.sent: list[str] = []
        self._in = list(inbound)

    def send(self, text):
        self.sent.append(text)

    def inbound(self, since):
        return [m for m in self._in if m.sent_at.date() >= since.date()]

    def close(self):
        pass

    def code(self):
        return re.findall(r"(?:OK|RESUME|FLATTEN|ARM) (\d{6}|\d{2})\b", self.sent[-1])[0]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports/rebalance").mkdir(parents=True)
    return OpsStore(tmp_path / "ops.db")


def msg(sid, body, minutes=0):
    return Inbound(sid, body, datetime.now(timezone.utc) + timedelta(minutes=minutes))


def test_pause_resume_requires_code(db):
    sms = FakeSms()
    assert "PAUSED" in sc.handle(db, sms, ap.parse("pause"), "SM1") and db.paused()
    assert sc.handle(db, sms, ap.parse("resume"), "SM2") == ""          # challenge text is the reply
    assert "RESUME" in sms.sent[-1] and db.paused()
    assert "RESUMED" in sc.handle(db, sms, ap.parse(f"RESUME {sms.code()}"), "SM3") and not db.paused()
    assert "Not understood" in sc.handle(db, sms, ap.parse("buy everything"), "SM4")


def test_poll_ignores_old_texts_and_handles_each_sid_once(db, monkeypatch):
    db.set_flag("control_since", datetime.now(timezone.utc).isoformat())
    old, new = msg("SMold", "pause", minutes=-5), msg("SMnew", "pause", minutes=1)
    sms = FakeSms([old, new, new])
    monkeypatch.setattr(sc, "Sms", lambda: sms)
    sc.cmd_poll(db)
    sc.cmd_poll(db)
    assert db.paused() and sum("PAUSED" in t for t in sms.sent) == 1
    with db.tx() as c:
        assert {r[0]: r[1] for r in c.execute("SELECT sid, outcome FROM inbound")} == {"SMold": None, "SMnew": "PAUSE"}


def test_book_approval_by_text_schedules_orders(db):
    sms = FakeSms()
    sc.book.propose(db, sms.send, strategy="megacap", targets={"NVDA": 0.2}, fill_session=date(2026, 11, 3),
                    deadline=datetime.now(timezone.utc) + timedelta(hours=1), label="megacap 11-02")
    reply = sc.handle(db, sms, ap.parse(f"ok {sms.code()}"), "SM1")
    assert "scheduled for 2026-11-03" in reply
    assert sc.book.projected(db, "megacap") == {"NVDA": 0.2}


def test_unclaimed_broker_approval_is_failed_not_retried(db, monkeypatch):
    sms = FakeSms()
    ap.request(db, sms.send, strategy="passive_60_40", kind="broker", period="2026-11", summary="60/40",
               payload={"plan": []}, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    _, row = ap.answer(db, ap.Command("OK", sms.code()), "SM1")
    calls = []
    monkeypatch.setitem(sc.EXECUTORS, "passive_60_40", lambda r: calls.append(r["id"]) or (True, "exit 0"))
    sc.dispatch_broker(db, sms)
    sc.dispatch_broker(db, sms)
    assert calls == [row["id"]] and ap.latest(db, "passive_60_40", "2026-11")["status"] == "failed"
    assert "not executed" in sms.sent[-1]


def test_tom_roles_match_the_registered_windows():
    assert sc.tom_role(date(2026, 11, 27)) == "entry"     # 2nd-to-last November session (11-26 holiday)
    assert sc.tom_role(date(2026, 12, 3)) == "exit"       # 3rd December session
    assert sc.tom_role(date(2026, 11, 26)) is None and sc.tom_role(date(2026, 11, 30)) is None


def order(sym, side, qty):
    return PlannedOrder(sym, side, qty, 100.0, qty * 100.0)


def test_rebalance_plan_must_match_approval():
    approved = [{"symbol": "SPY", "side": "sell", "qty": 30}, {"symbol": "IEF", "side": "buy", "qty": 100}]
    assert rp.same_plan(approved, [order("SPY", "sell", 32), order("IEF", "buy", 109)])
    assert not rp.same_plan(approved, [order("SPY", "sell", 30), order("IEF", "buy", 115)])
    assert not rp.same_plan(approved, [order("SPY", "buy", 30), order("IEF", "buy", 100)])
    assert not rp.same_plan(approved, [order("SPY", "sell", 30)])


def test_rebalance_gate_requests_then_runs_only_with_matching_claim(db, monkeypatch):
    sms = FakeSms()
    monkeypatch.setattr(rp, "sms_send", sms.send)
    monkeypatch.setattr(rp, "OpsStore", lambda: db)
    now = datetime(2026, 11, 2, 17, 0, tzinfo=timezone.utc)      # 12:00 ET: fixed (real clock failed after 23:00 ET)
    orders = [order("SPY", "sell", 30)]
    args = SimpleNamespace(approval_id=None)
    monkeypatch.setattr(rp, "WINDOW", (rp.WINDOW[0], (now + timedelta(hours=1)).astimezone(rp.ET).time()))
    et = now.astimezone(rp.ET)
    assert rp.approval_gate(args, orders, et, now) is None and "OK" in sms.sent[-1]
    assert rp.approval_gate(args, orders, et, now) is None and len(sms.sent) == 1   # pending: no re-ask
    _, row = ap.answer(db, ap.Command("OK", sms.code()), "SM1", now=now)
    with pytest.raises(SystemExit):
        rp.approval_gate(SimpleNamespace(approval_id=row["id"] + 1), orders, et, now)
    assert rp.approval_gate(SimpleNamespace(approval_id=row["id"]), orders, et, now) == row["id"]
    with pytest.raises(SystemExit):                                                   # claimed once only
        rp.approval_gate(SimpleNamespace(approval_id=row["id"]), orders, et, now)


def _full_session() -> date:
    """Next session with a 16:00 close (adversarial pass 4: a half-day next session made model tests date-dependent)."""
    from sgt.intraday.calendar import default_calendar
    cal, d = default_calendar(), sc.next_session(date.today())
    while cal.bounds(d).rth_close.astimezone(sc.ET).hour != 16:
        d = sc.next_session(d)
    return d


def test_megacap_proposal_is_sized_and_proposed_once(db, monkeypatch):
    import json

    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2025-06-02", periods=300).date
    m = rng.normal(0, 0.01, 300)

    def frame(beta, vol):
        c = 100 * np.cumprod(1 + beta * m + rng.normal(0, vol, 300))
        return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)
    bars = {"NVDA": frame(2.0, 0.01), "MSFT": frame(1.0, 0.005), "AAPL": frame(1.0, 0.005), "SPY": frame(1.0, 0.0)}
    monkeypatch.setattr(sc, "daily_bars", lambda syms: bars)
    dec_dir = Path("reports/megacap_forward/decisions")
    dec_dir.mkdir(parents=True)
    d = _full_session().isoformat()                         # a 16:00-close session; the clock below is 15:51 ET
    live_at = datetime.fromisoformat(d).replace(hour=15, minute=51, tzinfo=sc.ET)
    (dec_dir / f"{d}.json").write_text(json.dumps({"date": d, "mode": "realtime", "written_at": live_at.replace(minute=50).isoformat(), "positions": {
        "NVDA": {"position": 1}, "MSFT": {"position": -1}, "AAPL": {"position": 0}}}))
    sms = FakeSms()
    monkeypatch.setattr(sc, "Sms", lambda: sms)
    sc.cmd_megacap(db, now=live_at)
    sc.cmd_megacap(db, now=live_at)
    # owner 2026-11-09: model calls execute automatically (no approval), plus the overlay book
    assert ap.pending(db) == [] and len(sms.sent) == 2
    assert "megacap" in sms.sent[0] and "NVDA long" in sms.sent[0] and "MSFT short" in sms.sent[0]
    with db.tx() as c:
        rows = c.execute("SELECT strategy, symbol, target FROM book_orders WHERE status='scheduled'").fetchall()
    t = {(r["strategy"], r["symbol"]): r["target"] for r in rows}
    assert 0 < t[("megacap", "NVDA")] <= 0.25 and -0.25 <= t[("megacap", "MSFT")] < 0
    assert ("megacap", "AAPL") not in t
    assert t[(sc.OVERLAY, "NVDA")] == t[(sc.OVERLAY, "AAPL")] == round(1 / 3, 4)    # hold equally ...
    assert (sc.OVERLAY, "MSFT") not in t                                            # ... cash on short days


def test_megacap_ext_proposal_is_sized_and_proposed_once(db, monkeypatch):
    # prereg_megacap_ext_forward_2026-11-09: the 15-name set as its own book 'megacap_ext', sized under the
    # same MEGACAP_SIZING caps, auto-executed, and no overlay (that is the six-name prereg's).
    import json

    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2025-06-02", periods=300).date
    m = rng.normal(0, 0.01, 300)

    def frame(beta, vol):
        c = 100 * np.cumprod(1 + beta * m + rng.normal(0, vol, 300))
        return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)
    bars = {"AMD": frame(2.0, 0.01), "JPM": frame(1.0, 0.005), "WMT": frame(1.0, 0.005), "SPY": frame(1.0, 0.0)}
    monkeypatch.setattr(sc, "daily_bars", lambda syms: bars)
    dec_dir = Path("reports/megacap_ext/decisions")
    dec_dir.mkdir(parents=True)
    d = _full_session().isoformat()                         # a 16:00-close session; the clock below is 15:51 ET
    live_at = datetime.fromisoformat(d).replace(hour=15, minute=51, tzinfo=sc.ET)
    (dec_dir / f"{d}.json").write_text(json.dumps({"date": d, "mode": "realtime", "written_at": live_at.replace(minute=50).isoformat(), "positions": {
        "AMD": {"position": 1}, "JPM": {"position": -1}, "WMT": {"position": 0}}}))
    sms = FakeSms()
    monkeypatch.setattr(sc, "Sms", lambda: sms)
    sc.cmd_megacap_ext(db, now=live_at)
    sc.cmd_megacap_ext(db, now=live_at)
    # auto-executed (no approval) and proposed once; no overlay book for the extended set
    assert ap.pending(db) == [] and len(sms.sent) == 1
    assert "megacap_ext" in sms.sent[0] and "AMD long" in sms.sent[0] and "JPM short" in sms.sent[0]
    with db.tx() as c:
        rows = c.execute("SELECT strategy, symbol, target FROM book_orders WHERE status='scheduled'").fetchall()
    t = {(r["strategy"], r["symbol"]): r["target"] for r in rows}
    assert 0 < t[("megacap_ext", "AMD")] <= 0.25 and -0.25 <= t[("megacap_ext", "JPM")] < 0
    assert ("megacap_ext", "WMT") not in t
    assert all(strat != sc.OVERLAY for strat, _ in t)       # extended set runs no timing overlay


def test_digest_reports_failed_jobs_and_pending(db, tmp_path):
    from sgt.ops import digest

    now = datetime.now(rp.ET)
    log = tmp_path / "x.log"
    log.write_text(f"=== {now - timedelta(hours=30):%Y-%m-%d %H:%M:%S} old\n!!! exit 1\n"
                   f"=== {now - timedelta(hours=1):%Y-%m-%d %H:%M:%S} new\nok\n"
                   f"=== {now - timedelta(minutes=5):%Y-%m-%d %H:%M:%S} newer\n!!! exit 2\n")
    assert digest.job_failures(now, {"job": log}) == [f"job {now - timedelta(minutes=5):%m-%d %H:%M}"]
    ap.request(db, lambda t: None, strategy="tom", kind="book", period="p", summary="tom",
               payload={}, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
    text = digest.build(db, now, fills=[], problems=["x: missing close"], broker="Alpaca paper: $1", research=[None])
    # 2026-11-09 layout: what needs the owner comes first, before TODAY, so a long digest can never push it out
    needs = text.split("TODAY")[0]
    assert "\U0001F6A8" in needs and "\u2022 pending tom: OK " in needs and "\u2022 x: missing close" in needs
    assert "Alpaca paper: $1" in text and " today " in text.splitlines()[0]


# --- regression tests for the 2026-11-02 security review (F1-F4)
def test_flatten_broker_error_still_pauses_finishes_and_replies(db, monkeypatch):
    sms = FakeSms()
    sc.handle(db, sms, ap.parse("flatten"), "SM1")
    def boom(*a, **k):
        raise sc.httpx.ConnectError("dns")
    monkeypatch.setattr(sc.httpx, "delete", boom)
    reply = sc.handle(db, sms, ap.parse(f"FLATTEN {sms.code()}"), "SM2")
    assert "INCOMPLETE" in reply and db.paused()
    assert ap.latest(db, "control:flatten", datetime.now(timezone.utc).date().isoformat())["status"] == "failed"


def test_pause_blocks_pending_and_scheduled_entries(db):
    sms = FakeSms()
    sc.book.propose(db, sms.send, strategy="tom", targets={"SPY": 1.0}, fill_session=date(2026, 11, 3),
                    deadline=datetime.now(timezone.utc) + timedelta(hours=1), label="tom")
    code = sms.code()
    sc.handle(db, sms, ap.parse("pause"), "SM1")
    assert "Too late, not approved" in sc.handle(db, sms, ap.parse(f"OK {code}"), "SM2")   # PAUSE expired it
    assert sc.book.projected(db, "tom") == {}
    # an entry approved before PAUSE is cancelled by it
    db.set_flag("paused", "0")
    sc.book.propose(db, sms.send, strategy="tom", targets={"SPY": 1.0}, fill_session=date(2026, 11, 3),
                    deadline=datetime.now(timezone.utc) + timedelta(hours=1), label="tom")
    sc.handle(db, sms, ap.parse(f"OK {sms.code()}"), "SM3")
    assert sc.book.projected(db, "tom") == {"SPY": 1.0}
    assert "1 approved entries cancelled" in sc.handle(db, sms, ap.parse("pause"), "SM4")
    assert sc.book.projected(db, "tom") == {}


def test_replies_are_capped_and_challenges_not_replaced(db):
    sms = FakeSms()
    for _ in range(3):
        sc.send_capped(db, sms, "Not understood", unparsed=True)
    assert len(sms.sent) == 1
    for i in range(sc.REPLY_CAP_PER_HOUR + 5):
        sc.send_capped(db, sms, f"r{i}")
    assert len(sms.sent) == sc.REPLY_CAP_PER_HOUR
    sms2 = FakeSms()
    sc.handle(db, sms2, ap.parse("resume"), "SM1")
    first = sms2.code()
    assert "already sent" in sc.handle(db, sms2, ap.parse("resume"), "SM2")
    assert ap.answer(db, ap.Command("RESUME", first), "SM3")[1]["status"] == "approved"


def test_stale_approvals_are_swept_and_dispatch_survives_twilio_failure(db, monkeypatch):
    sms = FakeSms()
    now = datetime.now(timezone.utc)
    ap.request(db, sms.send, strategy="passive_60_40", kind="broker", period="2026-11", summary="60/40",
               payload={"plan": []}, expires_at=now + timedelta(minutes=1), now=now)
    ap.answer(db, ap.Command("OK", sms.code()), "SM1", now=now)
    swept = ap.sweep(db, now + timedelta(minutes=2))
    assert [r["status"] for r in swept] == ["approved"]
    assert ap.latest(db, "passive_60_40", "2026-11")["status"] == "failed"

    class Broken(FakeSms):
        def inbound(self, since):
            raise RuntimeError("twilio list HTTP 500")
    ap.request(db, sms.send, strategy="passive_60_40", kind="broker", period="2026-12", summary="60/40",
               payload={"plan": []}, expires_at=now + timedelta(hours=1), now=now)
    ap.answer(db, ap.Command("OK", sms.code()), "SM2")
    calls = []
    monkeypatch.setitem(sc.EXECUTORS, "passive_60_40", lambda r: calls.append(r["id"]) or (True, "exit 0"))
    monkeypatch.setattr(sc, "Sms", lambda: Broken())
    with pytest.raises(RuntimeError):
        sc.cmd_poll(db)
    assert calls                                     # approved order still dispatched


def test_last_final_session_uses_rth_close():
    # regression 2026-11-02: SessionBounds has rth_close, not close; the first live digest crashed on it
    et = sc.ET
    assert sc.last_final_session(datetime(2026, 11, 2, 20, 50, tzinfo=et)) == date(2026, 11, 2)
    assert sc.last_final_session(datetime(2026, 11, 2, 16, 5, tzinfo=et)) == date(2026, 10, 30)
    assert sc.last_final_session(datetime(2026, 11, 1, 12, 0, tzinfo=et)) == date(2026, 10, 30)


def test_arm_needs_code_is_blocked_while_paused_and_disarm_is_immediate(db):
    sms = FakeSms()
    assert sc.handle(db, sms, ap.parse("arm"), "SM1") == "" and "Reply ARM" in sms.sent[-1]
    assert "ARMED for" in sc.handle(db, sms, ap.parse(f"ARM {sms.code()}"), "SM2") and ap.armed(db)
    assert "DISARMED" in sc.handle(db, sms, ap.parse("disarm"), "SM3") and not ap.armed(db)
    sc.handle(db, sms, ap.parse("pause"), "SM4")
    sc.handle(db, sms, ap.parse("arm"), "SM5")
    assert "PAUSED" in sc.handle(db, sms, ap.parse(f"ARM {sms.code()}"), "SM6") and not ap.armed(db)
    assert "ARM 123456" not in ap.HELP and "ARM" in ap.HELP


@pytest.mark.parametrize("when", [
    datetime(2026, 11, 2, 20, 50),    # weekday digest time (the 11-02 crash)
    datetime(2026, 11, 2, 16, 5),     # before the close is final
    datetime(2026, 11, 7, 20, 50),    # Saturday
    datetime(2026, 11, 27, 20, 50),   # day after Thanksgiving (early close)
])
def test_digest_job_runs_end_to_end_without_network(db, monkeypatch, when):
    # regression 2026-11-03: cmd_digest crashed in production (SessionBounds.close); run the whole job
    sent = FakeSms()

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.replace(tzinfo=sc.ET).astimezone(tz) if tz else when

    def no_broker(*a, **k):
        raise sc.httpx.ConnectError("offline")
    monkeypatch.setattr(sc, "datetime", Clock)
    monkeypatch.setattr(sc, "Sms", lambda: sent)
    monkeypatch.setattr(sc, "daily_closes", lambda symbols, start, end: {})
    monkeypatch.setattr(sc, "paper_get", no_broker)
    assert sc.cmd_digest(db) == 0
    (text,) = sent.sent
    assert text.startswith("[SGT]") and "could not reach Alpaca" in text
    assert "Error" not in text and "Traceback" not in text


def test_exit_offer_replies_keep_or_exit_and_executes_nothing(db):
    # amendment F5: OK on an exit offer keeps the trade, NO exits it (the fade listener acts on the NO)
    sms = FakeSms()
    exp = datetime.now(timezone.utc) + timedelta(minutes=30)
    for sym in ("KEP", "OUT"):
        ap.request(db, sms.send, strategy=f"fade:{sym}", kind="fadeexit", period="2026-11-05", summary=f"IN {sym}",
                   payload={"fade_id": 1, "symbol": sym}, expires_at=exp)
    keep, out = (re.search(r"NO (\d{2}) = exit now", t).group(1) for t in sms.sent)
    assert sc.handle(db, sms, ap.parse(f"ok {keep}"), "SM1") == "KEEP: KEP stays short and exits by itself."
    assert sc.handle(db, sms, ap.parse(f"no {out}"), "SM2") == "EXIT: closing OUT at the next price (about 10 s)."
    assert sc.handle(db, sms, ap.parse(f"no {out}"), "SM3") == "No change: OUT is already exiting on your NO."


def _real_code(sms):
    return re.findall(r"REAL (\d{6})\b", sms.sent[-1])[0]


def test_real_needs_code_server_switch_and_paper_is_immediate(db, monkeypatch):
    """Live pilot 2026-11-05: REAL turns on real-money fade shorts only with a code and only when the server has
    the Schwab pilot enabled; PAPER switches it off at once; RESUME clears the pilot kill flag."""
    sms = FakeSms()
    monkeypatch.delenv("SGT_PILOT_SCHWAB", raising=False)
    assert sc.handle(db, sms, ap.parse("real"), "SM1") == ""
    assert "not enabled" in sc.handle(db, sms, ap.parse(f"REAL {_real_code(sms)}"), "SM2")
    assert db.flag("pilot_fade_live") != "1"
    monkeypatch.setenv("SGT_PILOT_SCHWAB", "1")
    sc.handle(db, sms, ap.parse("real"), "SM3")
    assert "REAL ON" in sc.handle(db, sms, ap.parse(f"REAL {_real_code(sms)}"), "SM4")
    assert db.flag("pilot_fade_live") == "1"
    assert "PAPER" in sc.handle(db, sms, ap.parse("paper"), "SM5") and db.flag("pilot_fade_live") == "0"
    sc.handle(db, sms, ap.parse("pause"), "SM6")
    sc.handle(db, sms, ap.parse("real"), "SM7")
    assert "PAUSED" in sc.handle(db, sms, ap.parse(f"REAL {_real_code(sms)}"), "SM8")
    db.set_flag("pilot_kill", "1")
    sc.handle(db, sms, ap.parse("resume"), "SM9")
    sc.handle(db, sms, ap.parse(f"RESUME {sms.code()}"), "SM10")
    assert db.flag("pilot_kill") == "0"


def test_flatten_also_closes_schwab_pilot_and_reports_failure(db, monkeypatch):
    sms = FakeSms()
    monkeypatch.setenv("SGT_PILOT_SCHWAB", "1")
    calls = []

    async def fake_flatten(d, send):
        calls.append(d)
        return ["Schwab: ABCD: BUY_TO_COVER 100 @ 10.05 sent"]
    monkeypatch.setattr(sc.pilot_runtime, "flatten", fake_flatten)
    sc.handle(db, sms, ap.parse("flatten"), "SM1")
    reply = sc.handle(db, sms, ap.parse(f"FLATTEN {sms.code()}"), "SM2")
    assert calls and "Schwab: ABCD: BUY_TO_COVER 100" in reply and db.paused()

    async def failed(d, send):
        return ["Schwab: NOT connected, pilot positions NOT closed; close them in the Schwab app"]
    monkeypatch.setattr(sc.pilot_runtime, "flatten", failed)
    sc.handle(db, sms, ap.parse("flatten"), "SM3")
    reply = sc.handle(db, sms, ap.parse(f"FLATTEN {sms.code()}"), "SM4")
    assert "INCOMPLETE" in reply and "pilot positions NOT closed" in reply



def test_model_trades_fill_today_only_when_decided_live():
    # 2026-11-09: a decision written after the close used to fill at the NEXT session's close (one day late)
    from datetime import date as _d
    d = _d(2026, 11, 10)
    at = lambda h, m: datetime(2026, 11, 10, h, m, tzinfo=sc.ET)  # noqa: E731
    w = {"written_at": at(15, 50).isoformat()}
    assert sc.model_fill({"date": d.isoformat(), "mode": "realtime", **w}, at(15, 51)) == d
    assert sc.model_fill({"date": d.isoformat(), "mode": "realtime", **w}, at(16, 5)) is None     # after the close
    assert sc.model_fill({"date": d.isoformat(), "mode": "batch", **w}, at(15, 51)) is None       # late decision
    assert sc.model_fill({"date": "2026-11-09", "mode": "realtime", **w}, at(15, 51)) is None     # yesterday's call


def test_megacap_long_book_takes_long_calls_of_both_sets_only_when_both_are_live(db, monkeypatch):
    # prereg_megacap_long_forward_2026-11-13: +1 positions of both sets as book 'megacap_long'; shorts and flats are
    # flat; nothing is proposed unless BOTH sets have a live 15:50 decision for today; proposed once.
    import json

    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(5)
    idx = pd.bdate_range("2025-06-02", periods=300).date
    m = rng.normal(0, 0.01, 300)

    def frame(beta, vol):
        c = 100 * np.cumprod(1 + beta * m + rng.normal(0, vol, 300))
        return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)
    bars = {s: frame(1.2, 0.006) for s in ("NVDA", "MSFT", "AMD", "JPM", "WMT")} | {"SPY": frame(1.0, 0.0)}
    monkeypatch.setattr(sc, "daily_bars", lambda syms: bars)
    d = _full_session().isoformat()
    live_at = datetime.fromisoformat(d).replace(hour=15, minute=51, tzinfo=sc.ET)
    stamp = live_at.replace(minute=50).isoformat()

    def write(folder, positions, mode="realtime"):
        Path(folder).mkdir(parents=True, exist_ok=True)
        (Path(folder) / f"{d}.json").write_text(json.dumps(
            {"date": d, "mode": mode, "written_at": stamp, "positions": {s: {"position": p} for s, p in positions.items()}}))
    sms = FakeSms()
    monkeypatch.setattr(sc, "Sms", lambda: sms)
    write(sc.LONG_SETS[0], {"NVDA": 1, "MSFT": -1})
    sc.cmd_megacap_long(db, now=live_at)                    # the other set has no decision: no order
    write(sc.LONG_SETS[1], {"AMD": 1, "JPM": -1, "WMT": 0}, mode="batch")
    sc.cmd_megacap_long(db, now=live_at)                    # the other set is not live: no order
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM book_orders").fetchone()[0] == 0
    write(sc.LONG_SETS[1], {"AMD": 1, "JPM": -1, "WMT": 0})
    sc.cmd_megacap_long(db, now=live_at)
    sc.cmd_megacap_long(db, now=live_at)                    # proposed once
    with db.tx() as c:
        rows = c.execute("SELECT strategy, symbol, target FROM book_orders WHERE status='scheduled'").fetchall()
    t = {r["symbol"]: r["target"] for r in rows}
    assert {r["strategy"] for r in rows} == {sc.LONG_BOOK} and set(t) == {"NVDA", "AMD"}       # longs only
    assert all(0 < w <= 0.25 for w in t.values()) and len(sms.sent) == 1 and "megacap long calls" in sms.sent[0]
