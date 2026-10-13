"""S-2 regression: bounded reads abort mid-stream instead of buffering huge bodies."""
from __future__ import annotations

import httpx
import pytest

from sgt.core.http import ResponseTooLarge, get_bounded


def _client(chunks_served: list[int], *, endless: bool, body: bytes = b"") -> httpx.Client:
    def stream():
        if not endless:
            yield body
            return
        while True:  # an upstream that never stops sending
            chunks_served[0] += 1
            yield b"x" * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=stream())

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_endless_body_aborts_at_cap():
    served = [0]
    with pytest.raises(ResponseTooLarge):
        get_bounded(_client(served, endless=True), "https://example.test/x", max_bytes=10_000)
    assert served[0] <= 11  # stopped right after crossing the cap, not after buffering everything


def test_small_body_returned_intact():
    resp = get_bounded(_client([0], endless=False, body=b'{"ok": true}'), "https://example.test/x",
                       max_bytes=10_000)
    assert resp.status_code == 200 and resp.content == b'{"ok": true}'
