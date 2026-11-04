"""SMS approvals (src/sgt/ops/approvals.py): strict parsing, single-use codes, expiry, lockout."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


class Outbox(list):
    def __call__(self, text: str) -> None:
        self.append(text)

    def code(self) -> str:
        return re.findall(r"(?:OK|RESUME|FLATTEN) (\d{6}|\d{2})\b", self[-1])[0]


def req(db, out, strategy="s", now=T0, ttl=timedelta(hours=2), kind="book"):
    return ap.request(db, out, strategy=strategy, kind=kind, period="p", summary=f"{strategy} trade",
                      payload={"targets": {"X": 0.1}, "fill_session": "2026-11-03"}, expires_at=now + ttl, now=now)


@pytest.mark.parametrize("body,expected", [
    ("ok 123456", ("OK", "123456")), ("  Ok   123456 ", ("OK", "123456")), ("NO 000001", ("NO", "000001")),
    ("status", ("STATUS", None)), ("?", ("CMDS", None)), ("resume", ("RESUME", None)),
    ("RESUME 123456", ("RESUME", "123456")),
    ("YES 123456", None), ("ok 12345", None), ("ok 1234567", None), ("ok 123456; drop table", None),
    ("ok 42", ("OK", "42")), ("no 42", ("NO", "42")), ("ok 4", None), ("ok 423", None), ("ARM 42", None),
    ("STOP", None), ("", None), ("x" * 500, None)])
def test_parse_is_strict(body, expected):
    cmd = ap.parse(body)
    assert (cmd.verb, cmd.code) == expected if expected else cmd is None


def test_ok_approves_once_and_newer_request_supersedes(db):
    out = Outbox()
    first = req(db, out)
    c1 = out.code()
    second = req(db, out, now=T0 + timedelta(minutes=1))
    c2 = out.code()
    reply, row = ap.answer(db, ap.Command("OK", c1), "SM1", now=T0 + timedelta(minutes=2))
    assert row is None and "replaced by a newer request" in reply   # superseded code is dead
    reply, row = ap.answer(db, ap.Command("OK", c2), "SM2", now=T0 + timedelta(minutes=2))
    assert row["id"] == second and row["status"] == "approved" and first != second
    assert ap.answer(db, ap.Command("OK", c2), "SM3", now=T0 + timedelta(minutes=3))[1] is None  # single use
    t = T0 + timedelta(minutes=3)
    assert ap.claim(db, second, now=t) and not ap.claim(db, second, now=t)


def test_no_rejects_and_expiry_fails_closed(db):
    out = Outbox()
    req(db, out, strategy="a")
    _, row = ap.answer(db, ap.Command("NO", out.code()), "SM1", now=T0)
    assert row["status"] == "rejected"
    req(db, out, strategy="b", ttl=timedelta(minutes=5))
    late = T0 + timedelta(minutes=6)
    assert ap.answer(db, ap.Command("OK", out.code()), "SM2", now=late)[1] is None
    assert ap.latest(db, "b", "p")["status"] == "expired"


def test_control_code_cannot_approve_trades_and_vice_versa(db):
    out = Outbox()
    ap.challenge(db, out, "resume", now=T0)
    resume_code = out.code()
    assert ap.answer(db, ap.Command("OK", resume_code), "SM1", now=T0)[1] is None
    req(db, out)
    assert ap.answer(db, ap.Command("RESUME", out.code()), "SM2", now=T0)[1] is None
    assert ap.answer(db, ap.Command("FLATTEN", resume_code), "SM3", now=T0)[1] is None
    _, row = ap.answer(db, ap.Command("RESUME", resume_code), "SM4", now=T0)
    assert row["status"] == "approved"


def test_five_wrong_codes_pause_and_expire_everything(db):
    out = Outbox()
    req(db, out)
    for i in range(ap.MAX_BAD_CODES):
        reply, _ = ap.answer(db, ap.Command("OK", f"{i:06d}" if f"{i:06d}" != out.code() else "999999"), f"SM{i}",
                             now=T0)
    assert "PAUSED" in reply and db.paused()
    assert ap.pending(db) == []
    assert req(db, out) is None                                     # paused: no new trade requests
    assert ap.challenge(db, out, "resume", now=T0) is not None      # but RESUME can still be challenged


def test_send_failure_leaves_request_unapprovable(db):
    def boom(text):
        raise RuntimeError("twilio down")
    assert req(db, boom) is None
    with db.tx() as c:
        assert c.execute("SELECT status FROM approvals").fetchone()[0] == "failed"


def test_trade_codes_are_two_digits_never_reused_and_late_replies_are_not_attacks(db):
    # owner request 2026-11-04: "OK 42"; a late reply must not count toward the wrong-code lockout
    out = Outbox()
    codes = set()
    for i in range(20):
        req(db, out, strategy=f"s{i}", ttl=timedelta(minutes=5), now=T0 + timedelta(minutes=i))
        c = out.code()
        assert len(c) == 2 and 10 <= int(c) <= 99 and c not in codes
        codes.add(c)
    late = T0 + timedelta(hours=1)
    for c in list(codes)[:ap.MAX_BAD_CODES + 1]:
        reply, row = ap.answer(db, ap.Command("OK", c), "SMx", now=late)
        assert row is None and reply.startswith("Too late, not approved")
    assert db.flag("paused") in (None, "0")                         # no lockout from late replies
    reply, _ = ap.answer(db, ap.Command("OK", "00"), "SMy", now=late)
    assert "No pending" in reply                                    # an unknown code is still a wrong code
