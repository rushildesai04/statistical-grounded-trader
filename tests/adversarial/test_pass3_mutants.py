"""Adversarial PASS 3 - mutation-derived regression tests.

Each test exists because a planted mutant SURVIVED the existing suite: it passes on the real code and
fails on the specific wrong implementation named in its docstring. Prior passes did not cover these.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import trade_analyst as ta  # noqa: E402

from sgt.ai import trade_review as tr  # noqa: E402
from sgt.fast import scanner  # noqa: E402
from sgt.ops import journal  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def test_scanner_nav_compounds_by_weight_not_raw_net(db):
    """Kills scanner.nav(): v *= 1 + weight*net dropped to v *= 1 + net.
    A +10% official net at 0.5% weight moves a $100k book by $50, not $10k."""
    with db.tx() as c:
        c.execute("INSERT INTO scan_trades(symbol, session, bar_ts, detected_at, signal_px, status, weight, net, "
                  "settled_at, created_at) VALUES ('SPK','2026-11-09','x','x',5.0,'settled',0.005,0.10,'t','t')")
    assert scanner.nav(db) == pytest.approx(100_000.0 * (1 + 0.005 * 0.10))


def test_systemic_classifies_rate_limit_model_and_models_errors():
    """Kills trade_review.systemic() dropping '-> 429' (A4) or 'not available' (A5): a rate-limited or
    model-not-available failure is a desk problem (pause, retry later), not the trade's fault."""
    assert tr.systemic(Exception("OpenAI chat -> 429 rate_limit"))
    assert tr.systemic(Exception("model gpt-x not available; set the model env to ..."))
    assert tr.systemic(Exception("OpenAI chat -> 403 forbidden"))
    assert tr.systemic(Exception("OpenAI /models -> 500"))
    assert tr.systemic(Exception("daily AI token budget 400000 reached"))
    assert not tr.systemic(Exception("answer is not JSON"))


def test_pending_stops_retrying_at_max_attempts(db):
    """Kills trade_review.pending(): n >= MAX_ATTEMPTS relaxed to n > MAX_ATTEMPTS would review a 4th time."""
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "created_at) VALUES ('OMH','2026-11-10','new_high','x','c',1,'est_closed','x')")
        c.execute("INSERT INTO trade_ai(key, model, at, status, attempts, body) "
                  "VALUES ('fade:1','m','t','error',2,'{}')")
    assert [e["key"] for e in tr.pending(db)] == ["fade:1"]
    with db.tx() as c:
        c.execute("UPDATE trade_ai SET attempts=3 WHERE key='fade:1'")
    assert tr.pending(db) == []


def test_payload_only_includes_trades_closed_after_last_run(db):
    """Kills trade_analyst.payload() since-filter: dropping or inverting the exit_at > since test re-sends
    every old trade to the model on the nightly incremental run."""
    ins = ("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
           "est_exit_at, net, created_at) VALUES (?,?, 'new_high','x','c',?,'settled',?,0.01,'x')")
    with db.tx() as c:
        c.execute(ins, ("OLD", "2026-11-08", 1, "2026-11-08T21:00:00+00:00"))
        c.execute(ins, ("NEW", "2026-11-10", 2, "2026-11-10T21:00:00+00:00"))
    data = ta.payload(db, "2026-11-09T00:00:00+00:00")
    syms = set()
    for t in data["trades"]:
        syms.add(t["symbol"])
    assert "NEW" in syms
    assert "OLD" not in syms


def test_scheduled_book_orders_appear_in_the_journal(db):
    """Kills journal.entries(): status IN ('filled','scheduled') narrowed to filled-only would hide every
    auto-scheduled (not-yet-filled) model-book order from the Notes page."""
    with db.tx() as c:
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, status, created_at, "
                  "label) VALUES ('megacap','NVDA',0.2,'2026-11-10','auto','scheduled','t','megacap 11-10')")
    keys = [e["key"] for e in journal.entries(db)]
    assert "book:1" in keys


# P3-6 (adversarial pass 3): the turn-of-month and ETF books trade automatically (owner 2026-11-09). Without this, a
# flip to approval texts (auto_all=False) survived every test.
def _auto_env(monkeypatch, sc, at):
    class Clock(sc.datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is None else at.astimezone(tz)

    class NoSms:
        def send(self, *a, **k):
            raise AssertionError("auto books must not text through this path")

        def close(self):
            pass

    calls = []
    monkeypatch.setattr(sc, "datetime", Clock)
    monkeypatch.setattr(sc, "Sms", NoSms)
    monkeypatch.setattr(sc.book, "propose", lambda db, send, **kw: calls.append(kw) or "ok")
    return calls


def test_tom_and_etf_books_propose_with_auto_all(tmp_path, monkeypatch):
    import sms_control as sc
    from sgt.ops.store import OpsStore

    at = sc.datetime(2026, 11, 27, 10, 0, tzinfo=sc.ET)
    calls = _auto_env(monkeypatch, sc, at)
    monkeypatch.setattr(sc, "tom_role", lambda d: "entry")
    monkeypatch.setattr(sc, "TOM_FIRST_ENTRY", at.date())
    monkeypatch.setattr(sc, "deadline", lambda d: at + sc.timedelta(hours=6))
    db = OpsStore(tmp_path / "ops.db")
    assert sc.cmd_tom(db) == 0
    assert len(calls) == 1 and calls[0]["strategy"] == "tom" and calls[0]["auto_all"] is True
    assert calls[0]["targets"] == {"SPY": 1.0}
    assert sc.cmd_tom(db) == 0 and len(calls) == 1                  # once per session

    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports" / "forward").mkdir(parents=True)
    (tmp_path / "reports" / "forward" / "2026-11.json").write_text(
        sc.json.dumps({"weights": {"core": {"SPY": 0.6, "TLT": 0.4}}}))
    monkeypatch.setattr(sc, "fill_session_for", lambda now: now.date())
    calls.clear()
    assert sc.cmd_etf(db) == 0
    assert len(calls) == 1 and calls[0]["strategy"] == "etf:core" and calls[0]["auto_all"] is True
    assert sc.cmd_etf(db) == 0 and len(calls) == 1                  # once per weights file
