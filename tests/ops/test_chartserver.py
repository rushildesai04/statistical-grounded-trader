from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

import httpx
import pytest

from sgt.ops import chartserver

TOKEN = "t" * 40


@pytest.fixture
def server(tmp_path):
    page = tmp_path / "fade_tape.html"
    srv = ThreadingHTTPServer(("127.0.0.1", 0), chartserver.make_handler(TOKEN, page))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=5) as c:
        yield c, page
    srv.shutdown()
    srv.server_close()


def test_only_the_secret_path_serves_the_page_with_strict_headers(server):
    c, page = server
    page.write_text("<title>Fade Tape</title>ok")
    r = c.get(f"/{TOKEN}/")
    assert r.status_code == 200 and r.text.endswith("ok") and r.headers["content-type"].startswith("text/html")
    assert r.headers["referrer-policy"] == "no-referrer" and r.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and "noindex" in r.headers["x-robots-tag"]
    assert c.head(f"/{TOKEN}/").status_code == 200
    assert c.get(f"/{TOKEN}").text == r.text                       # link pasted without the trailing slash


@pytest.mark.parametrize("path", ["/", f"/{TOKEN}//", f"/{TOKEN}/x", f"/{TOKEN}/?a=1", f"/{TOKEN[:-1]}/",
                                  "/../reports/charts/fade_tape.html"])
def test_everything_else_is_not_found(server, path):
    c, page = server
    page.write_text("secret")
    r = c.get(path)
    assert r.status_code == 404 and "secret" not in r.text


def test_writes_are_refused_and_missing_page_is_a_friendly_placeholder(server):
    c, _ = server
    assert c.post(f"/{TOKEN}/", content=b"x").status_code == 405
    assert c.request("DELETE", f"/{TOKEN}/").status_code == 405
    r = c.get(f"/{TOKEN}/")
    assert r.status_code == 200 and "No chart page yet" in r.text


def test_fails_closed_without_a_strong_token(monkeypatch):
    for bad in (None, "", "short", "x" * 31, "a" * 40 + "/"):
        assert not chartserver.valid_token(bad)
        with pytest.raises(ValueError):
            chartserver.make_handler(bad or "")
    monkeypatch.setattr(chartserver, "get_secret", lambda name, required=True: None)
    assert chartserver.main() == 2 and chartserver.link() == ""
    monkeypatch.setattr(chartserver, "get_secret", lambda name, required=True: TOKEN)
    assert chartserver.link("https://h.example/") == f"https://h.example/{TOKEN}/"


ORIGIN = "https://sgt.example"


@pytest.fixture
def api(tmp_path):
    from sgt.ops.store import OpsStore
    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "created_at) VALUES ('OMH','2026-11-09','new_high','x','c',1,'est_closed','x')")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), chartserver.make_handler(TOKEN, tmp_path / "p.html",
                                                                          db_factory=lambda: db, origin=ORIGIN))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    with httpx.Client(base_url=f"http://127.0.0.1:{srv.server_address[1]}", timeout=5) as c:
        yield c, db
    srv.shutdown()
    srv.server_close()


def post(c, body, origin=ORIGIN, ctype="application/json"):
    import json
    return c.post(f"/{TOKEN}/api/notes", content=json.dumps(body).encode(),
                  headers={"Origin": origin, "Content-Type": ctype})


def test_notes_api_saves_reads_and_keeps_history(api):
    c, db = api
    r = post(c, {"key": "fade:1", "text": "halted twice; entry before the halt\x00"})
    assert r.status_code == 200 and r.json()["text"].startswith("halted twice") and "\x00" not in r.json()["text"]
    got = c.get(f"/{TOKEN}/api/notes").json()
    assert got["notes"]["fade:1"]["text"].startswith("halted twice") and got["insights"] == []
    assert post(c, {"key": "fade:1", "text": ""}).status_code == 200                         # empty deletes
    assert c.get(f"/{TOKEN}/api/notes").json()["notes"] == {}
    with db.tx() as cur:
        assert cur.execute("SELECT COUNT(*) FROM trade_notes_history").fetchone()[0] == 2


@pytest.mark.parametrize("body,origin,ctype,code", [
    ({"key": "fade:1", "text": "x"}, "https://evil.example", "application/json", 403),   # cross-site
    ({"key": "fade:1", "text": "x"}, "", "application/json", 403),                        # no Origin
    ({"key": "fade:1", "text": "x"}, ORIGIN, "text/plain", 415),
    ({"key": "fade:99", "text": "x"}, ORIGIN, "application/json", 400),                   # unknown trade
    ({"key": "fade:1; DROP", "text": "x"}, ORIGIN, "application/json", 400),
    ({"key": "fade:1", "text": "x" * 5001}, ORIGIN, "application/json", 400),
    (["fade:1"], ORIGIN, "application/json", 400),
])
def test_notes_api_rejects_bad_writes(api, body, origin, ctype, code):
    c, _ = api
    assert post(c, body, origin=origin, ctype=ctype).status_code == code


def test_notes_api_bounds_body_and_rate_and_wrong_token(api, monkeypatch):
    c, _ = api
    r = c.post(f"/{TOKEN}/api/notes", content=b"{" + b" " * (chartserver.MAX_BODY + 1) + b"}",
               headers={"Origin": ORIGIN, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert c.post(f"/{'u' * 40}/api/notes", content=b"{}", headers={"Origin": ORIGIN}).status_code == 404
    assert c.get(f"/{'u' * 40}/api/notes").status_code == 404
    codes = [post(c, {"key": "fade:1", "text": f"n{i}"}).status_code for i in range(chartserver.WRITES_PER_MIN + 3)]
    assert 429 in codes


def test_notes_reads_are_rate_limited_too(api):
    c, _ = api
    codes = [c.get(f"/{TOKEN}/api/notes").status_code for _ in range(chartserver.READS_PER_MIN + 3)]
    assert codes[0] == 200 and 429 in codes
