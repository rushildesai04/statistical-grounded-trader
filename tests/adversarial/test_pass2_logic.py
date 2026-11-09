"""PASS 2 adversarial reproducers for the last-3-commit fixes."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from sgt.ai import openai_api as oa
from sgt.fast import scanner, xsocial
from sgt.intraday.calendar import default_calendar
from sgt.ops.store import OpsStore, iso

UTC = timezone.utc
ET = scanner.ET
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
SCHEMA = dict(name="r", strict=True, schema=dict(type="object"))


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def chat_client(status=200, tokens=1000, content='{"ok": 1}', refusal=None):
    def handler(req):
        if status != 200:
            return httpx.Response(status, json=dict(error=dict(code="boom")))
        msg = dict(refusal=refusal) if refusal else dict(content=content)
        usage = dict(total_tokens=tokens)
        choices = [dict(message=msg)]
        return httpx.Response(200, json=dict(choices=choices, usage=usage))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_budget_success_settles_to_real_usage(db):
    out = oa.chat_json(chat_client(tokens=1000), db, "m", "s", "u", SCHEMA, 200, NOW)
    assert out == dict(ok=1)
    assert oa.spent(db, NOW) == 1000


def test_budget_non_200_releases_reservation(db):
    with pytest.raises(oa.AIError):
        oa.chat_json(chat_client(status=500), db, "m", "s", "u", SCHEMA, 200, NOW)
    assert oa.spent(db, NOW) == 0


def test_budget_refusal_keeps_usage_and_is_aierror(db):
    with pytest.raises(oa.AIError) as ei:
        oa.chat_json(chat_client(tokens=1234, refusal="no"), db, "m", "s", "u", SCHEMA, 200, NOW)
    assert isinstance(ei.value, oa.BilledError)
    assert oa.spent(db, NOW) == 1234


def test_budget_bad_json_content_keeps_usage(db):
    with pytest.raises(oa.BilledError):
        oa.chat_json(chat_client(tokens=777, content="not json"), db, "m", "s", "u", SCHEMA, 200, NOW)
    assert oa.spent(db, NOW) == 777


def test_budget_fails_closed_once_counter_exceeds_cap(db, monkeypatch):
    monkeypatch.setenv("SGT_AI_DAILY_TOKENS", "300")
    oa.chat_json(chat_client(tokens=1000), db, "m", "s", "u", SCHEMA, 200, NOW)
    with pytest.raises(oa.AIError, match="budget"):
        oa.chat_json(chat_client(tokens=10), db, "m", "s", "u", SCHEMA, 200, NOW)
    assert oa.spent(db, NOW) == 1000


def test_take_enforces_cap_boundary(db):
    assert db.take("k", 3, 5) is True
    assert db.take("k", 2, 5) is True
    assert db.take("k", 1, 5) is False
    assert int(db.flag("k")) == 5


def test_x_cap_persists_across_processes(db, tmp_path):
    day = "2026-11-10"
    for _ in range(xsocial.DAILY_CAP):
        assert xsocial._take_call(day, db) is True
    assert xsocial._take_call(day, db) is False
    db2 = OpsStore(tmp_path / "ops.db")
    assert xsocial._take_call(day, db2) is False


def _insert(db, **cols):
    keys = ", ".join(cols)
    qs = ",".join("?" for _ in cols)
    with db.tx() as c:
        return c.execute("INSERT INTO scan_trades(" + keys + ") VALUES (" + qs + ")", tuple(cols.values())).lastrowid


def _row(db, rid):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM scan_trades WHERE id=?", (rid,)).fetchone())


def test_open_after_close_exits_at_est_last_not_late_print(db):
    session = "2026-11-06"
    entry_at = datetime(2026, 11, 6, 20, 30, tzinfo=UTC)
    bar_ts = entry_at - timedelta(minutes=1)
    rid = _insert(db, symbol="ABCD", session=session, bar_ts=iso(bar_ts), detected_at=iso(bar_ts),
                  signal_px=10.0, status="open", weight=0.005, est_entry=10.0, est_entry_at=iso(entry_at),
                  est_last=9.0, created_at=iso(bar_ts))
    now = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
    scanner.step(db, lambda s: 50.0, now)
    r = _row(db, rid)
    assert r["status"] == "est_closed" and r["reason"] == "close"
    assert r["est_exit"] == 9.0


def _find_early_close():
    cal = default_calendar()
    d = date(2026, 11, 20)
    for i in range(400):
        x = d + timedelta(days=i)
        if cal.is_session(x) and cal.bounds(x).rth_close.astimezone(ET).hour == 13:
            return x
    return None


def test_pending_not_opened_after_early_close(db):
    d = _find_early_close()
    assert d is not None
    bar_ts = datetime.combine(d, datetime.min.time(), ET).replace(hour=12, minute=58)
    rid = _insert(db, symbol="ABCD", session=d.isoformat(), bar_ts=iso(bar_ts), detected_at=iso(bar_ts),
                  signal_px=5.3, status="pending", weight=0.005, created_at=iso(bar_ts))
    now = datetime.combine(d, datetime.min.time(), ET).replace(hour=13, minute=1)
    scanner.step(db, lambda s: 5.3, now)
    assert _row(db, rid)["status"] != "open", "opened a scanner trade after the early close"
