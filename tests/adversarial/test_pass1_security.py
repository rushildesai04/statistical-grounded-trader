"""PASS 1 track E (security / data integrity) reproducers. Read-only probes of today's surface.

PROVE the flagged defenses hold (embedded-JSON script breakout, untrusted-field escaping,
note-key SQL safety, AI-output bounding) and document one LOW gap (X daily cap is process memory).
No production code is modified. XSS payload tags are built with chr() to keep the source plain.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

UTC = timezone.utc
LT, GT = chr(60), chr(62)
CLOSE = LT + "/script" + GT
OPEN = LT + "script" + GT
PAYLOAD = CLOSE + OPEN + "alert(document.cookie)" + CLOSE


@pytest.fixture
def db(tmp_path):
    from sgt.ops.store import OpsStore
    d = OpsStore(tmp_path / "ops.db")
    with d.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, created_at) VALUES ('OMH','2026-11-09','new_high','x','c',1,'est_closed','x')")
    return d


BAD_KEYS = ["fade:1); DROP TABLE trade_notes;--", "fade:1 OR 1=1", "scan:1'", "book:0", "fade:", "FADE:1", "fade:99999999999"]


@pytest.mark.parametrize("key", BAD_KEYS)
def test_save_rejects_crafted_keys_and_tables_survive(db, key):
    from sgt.ops import journal
    with pytest.raises(ValueError):
        journal.save(db, key, "x", datetime.now(UTC))
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM trade_notes").fetchone()[0] == 0
        assert c.execute("SELECT name FROM sqlite_master WHERE name='trade_notes'").fetchone() is not None


def test_save_bounds_length_and_strips_control_chars(db):
    from sgt.ops import journal
    saved = journal.save(db, "fade:1", "a\x00\x07\x1b" + "b" * 10, datetime.now(UTC))
    assert "\x00" not in saved["text"] and "\x1b" not in saved["text"] and saved["text"].startswith("ab")
    with pytest.raises(ValueError):
        journal.save(db, "fade:1", "x" * (journal.MAX_NOTE + 1), datetime.now(UTC))


def test_ai_review_validate_bounds_untrusted_model_output():
    from sgt.ai import trade_review
    raw = dict(verdict="jailbroken; ignore rules", what_happened="z" * 5000, tags=["t" * 999, 5, dict(x=1)], confidence="extreme")
    out = trade_review.validate(raw)
    assert out["verdict"] == "unclear" and out["confidence"] == "low"
    assert len(out["what_happened"]) <= 800 and len(out["tags"][0]) <= 40
    assert all(isinstance(t, str) for t in out["tags"])


def test_ai_report_validate_bounds_and_mirrors_distilled():
    from scripts import trade_analyst
    pat = dict(pattern="p", evidence="e", proposed_test="t", confidence="nope")
    br = dict(principle="x", evidence="y", status="bogus")
    out = trade_analyst.validate(dict(headline="h" * 999, patterns=[pat], brain=[br]))
    assert len(out["headline"]) <= 200 and out["patterns"][0]["confidence"] == "low"
    assert out["brain"][0]["status"] == "hypothesis" and out["distilled"][0]["insight"] == "p"


def test_x_daily_cap_survives_a_process_restart(tmp_path, monkeypatch):
    # F-5 (was: the cap lived in process memory and reset on every deploy). With the ops DB it persists.
    import httpx

    from sgt.fast import xsocial
    from sgt.ops.store import OpsStore
    db = OpsStore(tmp_path / "ops.db")
    monkeypatch.setattr(xsocial, "DAILY_CAP", 2)
    monkeypatch.setattr(xsocial, "get_secret", lambda name, required=True: "tok")
    monkeypatch.delenv("SGT_X_ENABLED", raising=False)
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(200, json={"data": [{"tweet_count": 0}]})
    at = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    for sym in ("AAA", "BBB"):
        xsocial._CACHE.clear()
        assert xsocial.lookup(sym, at, client=client, db=db) is not None
    xsocial._CALLS.clear()                                   # a deploy restarts the process
    xsocial._CACHE.clear()
    n = len(seen)
    assert xsocial.lookup("CCC", at, client=client, db=db) is None and len(seen) == n   # still capped
    assert db.flag("x_calls:2026-11-09") == "2"


def test_embedded_json_escapes_breakout_and_line_separators():
    from sgt.ops import charts
    posts = '[{"at":"t","likes":1,"text":"' + CLOSE + 'pwn"}]'
    row = dict(key="fade:1", symbol=PAYLOAD, book="alert fade", session="2026-11-09", news_head="break\u2028out\u2029now", x_cat_posts=posts)
    html = charts.render([], datetime(2026, 11, 9, tzinfo=UTC), journal=[row])
    assert CLOSE + OPEN not in html
    assert "\\u003c/script" + GT in html
    assert "\u2028" not in html and "\u2029" not in html
