"""SIZE text command (owner 2026-11-09): show, lower at once, raise only with a 6-digit code bound to the amount."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import sms_control as sc  # noqa: E402
from sgt.fast import fade  # noqa: E402
from sgt.ops import approvals as ap  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402


class FakeSms:
    def __init__(self):
        self.sent: list[str] = []

    def send(self, text):
        self.sent.append(text)

    def code(self):
        return re.findall(r"Reply SIZE (\d+) (\d{6})\b", self.sent[-1])[0]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return OpsStore(tmp_path / "ops.db")


@pytest.mark.parametrize("text,want", [
    ("size", ("SIZE", None, None)), ("SIZE 10000", ("SIZE", None, 10000)), ("size $10,000", ("SIZE", None, 10000)),
    ("SIZE 10000 123456", ("SIZE", "123456", 10000)), ("SIZE  2,500  654321", ("SIZE", "654321", 2500))])
def test_parse(text, want):
    c = ap.parse(text)
    assert (c.verb, c.code, c.amount) == want


@pytest.mark.parametrize("text", ["SIZE abc", "SIZE -5", "SIZE 10000 12", "SIZE 1,00", "SIZE 10000 123456 7",
                                  "SIZE 12345678"])
def test_parse_rejects(text):
    assert ap.parse(text) is None


def test_show_and_status(db):
    out = sc.handle(db, FakeSms(), ap.parse("SIZE"), "SM1")
    assert "fade size: $5,000/trade (5.0% of the $100,000 paper book, default)" in out
    assert "fade size: $5,000/trade" in sc.status_text(db)


def test_lowering_applies_at_once(db):
    out = sc.handle(db, FakeSms(), ap.parse("SIZE 2000"), "SM1")
    assert out.startswith("SIZE: new paper fade trades $2,000 each") and db.flag(fade.SIZE_FLAG) == "2000"
    assert fade.notional(db) == pytest.approx(2_000)


def test_raising_needs_the_code_for_that_amount(db):
    sms = FakeSms()
    assert sc.handle(db, sms, ap.parse("SIZE 10000"), "SM1") == ""
    assert "SIZE: paper fade trades $10,000 each" in sms.sent[-1] and db.flag(fade.SIZE_FLAG) is None
    usd, code = sms.code()
    assert usd == "10000"
    assert "A SIZE code was already sent" in sc.handle(db, sms, ap.parse("SIZE 20000"), "SM2")
    out = sc.handle(db, sms, ap.parse(f"SIZE 20000 {code}"), "SM3")          # code bound to $10,000, not $20,000
    assert "No pending request" in out and db.flag(fade.SIZE_FLAG) is None
    out = sc.handle(db, sms, ap.parse(f"ARM {code}"), "SM4")                 # a SIZE code cannot ARM
    assert "No pending request" in out and not ap.armed(db)
    out = sc.handle(db, sms, ap.parse(f"SIZE 10000 {code}"), "SM5")
    assert "SIZE: new paper fade trades $10,000 each" in out and fade.notional(db) == pytest.approx(10_000)
    db.set_flag(fade.SIZE_FLAG, "3000")                        # owner lowers later; replaying the code must not undo it
    out = sc.handle(db, sms, ap.parse(f"SIZE 10000 {code}"), "SM6")
    assert out.startswith("No change") and db.flag(fade.SIZE_FLAG) == "3000"                    # single use


@pytest.mark.parametrize("usd", [499, 25_001, 1_000_000])
def test_out_of_bounds_refused(db, usd):
    out = sc.handle(db, FakeSms(), ap.parse(f"SIZE {usd}"), "SM1")
    assert out.startswith("SIZE not changed: allowed $500 to $25,000") and db.flag(fade.SIZE_FLAG) is None


def test_cap_rechecked_when_the_code_comes_back(db):
    sms = FakeSms()
    sc.handle(db, sms, ap.parse("SIZE 25000"), "SM1")
    usd, code = sms.code()
    with db.tx() as c:                                        # the book loses 10% before the owner replies
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "weight, net, settled_at, created_at) VALUES ('L','2026-10-30','volume_spike','x','-1',1,'settled',"
                  "0.5,-0.2,'2026-10-30T16:00:00+00:00','x')")
    out = sc.handle(db, sms, ap.parse(f"SIZE {usd} {code}"), "SM2")
    assert out.startswith("SIZE not changed") and db.flag(fade.SIZE_FLAG) is None


def test_size_reply_says_when_the_drift_guard_halved_new_fades(db):
    from sgt.fast import fade
    from sgt.learning import live_monitor as lm
    assert "drift guard" not in sc.size_line(db)
    db.set_flag(lm.SCALE_FLAG + fade.BOOK, "0.5")
    assert sc.size_line(db).endswith("default; drift guard: new fades at 50% of it)")
