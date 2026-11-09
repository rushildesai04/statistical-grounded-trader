"""Web server for the Fade Tape page on the runner (owner decision 2026-11-05: secret link, option A).

2026-11-09 (owner: a Notes tab to write trade notes): besides the page, exactly two JSON routes under the same secret
prefix: GET ``/<token>/api/notes`` (notes + AI insights) and POST ``/<token>/api/notes`` (save one note). The POST is
the only write anywhere on this surface: JSON only, body <= 16 KB, same-origin only (Origin must equal the public
base), a write rate limit, notes bounded and validated against existing trades (sgt.ops.journal), every edit kept.

The runner's only public surface. It serves exactly one file at ``/<token>/`` (or ``/<token>``), where the token is
the secret ``charts_token`` (>= 32 URL-safe characters). Everything else is 404, including wrong tokens (compared in
constant time), other methods are 405, and nothing is ever written. No directory listing, no query handling, no
cookies. Responses carry no-store / no-referrer (the token is in the path and must not leak to the chart links on
the page) / noindex and a CSP that only admits the page's pinned script and font hosts. The server refuses to start
without a valid token (fail closed).
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from sgt.core.secrets import get_secret

log = logging.getLogger(__name__)

PAGE = Path("reports/charts/fade_tape.html")
PORT = 8080
MIN_TOKEN = 32
MAX_PAGE = 8 * 1024 * 1024
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")
CSP = ("default-src 'none'; script-src 'unsafe-inline' https://unpkg.com; style-src 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src data:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
           "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
           "Strict-Transport-Security": "max-age=31536000", "Content-Security-Policy": CSP}
EMPTY = (b"<!doctype html><meta charset=utf-8><title>Fade Tape</title>"
         b"<p style='font-family:system-ui'>No chart page yet. It is rebuilt every 10 minutes during market hours.</p>")


MAX_BODY = 16 * 1024
WRITES_PER_MIN = 60
READS_PER_MIN = 120                                # review 2026-11-09 F-1: reads take the DB lock too


class _Bucket:
    """Fixed-window write limiter shared by the server's threads."""

    def __init__(self, per_min: int) -> None:
        self.per_min, self.window, self.n, self.lock = per_min, 0, 0, threading.Lock()

    def allow(self) -> bool:
        with self.lock:
            w = int(time.monotonic() // 60)
            if w != self.window:
                self.window, self.n = w, 0
            self.n += 1
            return self.n <= self.per_min


def valid_token(token: str | None) -> bool:
    return bool(token) and len(token) >= MIN_TOKEN and bool(TOKEN_RE.fullmatch(token))


def make_handler(token: str, page: Path = PAGE, db_factory=None, origin: str | None = None):
    if not valid_token(token):
        raise ValueError(f"charts_token must be >= {MIN_TOKEN} URL-safe characters")
    want = f"/{token}/".encode()
    bare = want[:-1]                                   # 2026-11-05: owner's pasted link dropped the trailing slash
    api = f"/{token}/api/notes".encode()
    allowed_origin = (origin or os.environ.get("SGT_CHARTS_BASE", "https://sgt-runner.fly.dev")).rstrip("/")
    bucket, reads = _Bucket(WRITES_PER_MIN), _Bucket(READS_PER_MIN)

    def store():
        if db_factory is not None:
            return db_factory()
        from sgt.ops.store import OpsStore
        return OpsStore()

    class Handler(BaseHTTPRequestHandler):
        server_version = "sgt"
        sys_version = ""
        timeout = 10                                   # slow clients cannot hold a thread

        def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            for k, v in HEADERS.items():
                self.send_header(k, v)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _page(self) -> None:
            path = self.path.encode()
            if not (hmac.compare_digest(path, want) | hmac.compare_digest(path, bare)):
                self._send(404, b"not found")
                return
            try:
                size = page.stat().st_size
                body = page.read_bytes() if size <= MAX_PAGE else None
            except FileNotFoundError:
                body = EMPTY
            if body is None:
                log.error("chart page is %d bytes, over the %d limit", size, MAX_PAGE)
                self._send(503, b"page too large")
                return
            self._send(200, body, "text/html; charset=utf-8")

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def _is_api(self) -> bool:
            return hmac.compare_digest(self.path.encode(), api)

        def do_GET(self) -> None:                      # noqa: N802 - http.server API
            if self._is_api():
                if not reads.allow():
                    self._send(429, b"slow down")
                    return
                from sgt.ops import journal
                db = store()
                self._json(200, {"notes": journal.notes(db), "insights": journal.insights(db)})
                return
            self._page()

        def do_HEAD(self) -> None:                     # noqa: N802
            self._page()

        def do_POST(self) -> None:                     # noqa: N802
            if not self._is_api():
                path = self.path.encode()
                page_path = hmac.compare_digest(path, want) | hmac.compare_digest(path, bare)
                self._send(405 if page_path else 404, b"method not allowed" if page_path else b"not found")
                return
            if self.headers.get("Origin", "").rstrip("/") != allowed_origin:
                self._send(403, b"forbidden")
                return
            if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
                self._send(415, b"json only")
                return
            try:
                n = int(self.headers.get("Content-Length") or "-1")
            except ValueError:
                n = -1
            if not 0 < n <= MAX_BODY:
                self._send(413, b"too large")
                return
            if not bucket.allow():
                self._send(429, b"slow down")
                return
            try:
                body = json.loads(self.rfile.read(n))
                if not isinstance(body, dict):
                    raise ValueError("object expected")
                from sgt.ops import journal
                saved = journal.save(store(), body.get("key"), body.get("text"), datetime.now(timezone.utc))
            except (ValueError, UnicodeDecodeError) as exc:
                self._json(400, {"error": str(exc)[:100]})
                return
            log.info("note saved %s", saved["key"])
            self._json(200, saved)

        def _refuse(self) -> None:
            self._send(405, b"method not allowed")

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse  # noqa: N815

        def log_message(self, fmt: str, *args) -> None:  # never log the path: it holds the token
            log.info("%s %s", self.command, getattr(self, "_status", ""))

        def send_response(self, code: int, message: str | None = None) -> None:
            self._status = code
            super().send_response(code, message)

    return Handler


def link(base: str | None = None) -> str:
    """The owner's page link, or '' when the secret or the public host is not configured (texts then omit it)."""
    token = get_secret("charts_token", required=False)
    base = base or os.environ.get("SGT_CHARTS_BASE", "https://sgt-runner.fly.dev")
    return f"{base.rstrip('/')}/{token}/" if valid_token(token) else ""


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = get_secret("charts_token", required=False)
    if not valid_token(token):
        log.error("charts_token missing or too short; chart server not started")
        return 2
    ThreadingHTTPServer(("0.0.0.0", PORT), make_handler(token)).serve_forever()  # noqa: S104 - Fly proxy in front
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
