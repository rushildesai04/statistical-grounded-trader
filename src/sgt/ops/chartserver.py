"""Read-only web server for the Fade Tape page on the runner (owner decision 2026-11-05: secret link, option A).

The runner's only public surface. It serves exactly one file at exactly one path, ``/<token>/``, where the token is
the secret ``charts_token`` (>= 32 URL-safe characters). Everything else is 404, including wrong tokens (compared in
constant time), other methods are 405, and nothing is ever written. No directory listing, no query handling, no
cookies. Responses carry no-store / no-referrer (the token is in the path and must not leak to the chart links on
the page) / noindex and a CSP that only admits the page's pinned script and font hosts. The server refuses to start
without a valid token (fail closed).
"""
from __future__ import annotations

import hmac
import logging
import os
import re
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
       "https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src data:; connect-src 'none'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
           "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
           "Strict-Transport-Security": "max-age=31536000", "Content-Security-Policy": CSP}
EMPTY = (b"<!doctype html><meta charset=utf-8><title>Fade Tape</title>"
         b"<p style='font-family:system-ui'>No chart page yet. It is rebuilt every 10 minutes during market hours.</p>")


def valid_token(token: str | None) -> bool:
    return bool(token) and len(token) >= MIN_TOKEN and bool(TOKEN_RE.fullmatch(token))


def make_handler(token: str, page: Path = PAGE):
    if not valid_token(token):
        raise ValueError(f"charts_token must be >= {MIN_TOKEN} URL-safe characters")
    want = f"/{token}/".encode()

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
            if not hmac.compare_digest(self.path.encode(), want):
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

        def do_GET(self) -> None:                      # noqa: N802 - http.server API
            self._page()

        def do_HEAD(self) -> None:                     # noqa: N802
            self._page()

        def _refuse(self) -> None:
            self._send(405, b"method not allowed")

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse  # noqa: N815

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
