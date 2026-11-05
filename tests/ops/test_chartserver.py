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
