"""Bounded HTTP reads for untrusted/semi-trusted upstreams.

``httpx`` buffers whole bodies by default, so a post-hoc ``len(resp.content)`` check
only fires after a huge (or decompression-bomb) body is already in memory. These
helpers stream the DECODED body and abort as soon as ``max_bytes`` is exceeded
(2026-10-13, review S-2).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class ResponseTooLarge(Exception):
    def __init__(self, url: str, max_bytes: int) -> None:
        super().__init__(f"response from {url} exceeded {max_bytes} bytes")
        self.url = url
        self.max_bytes = max_bytes


@dataclass(frozen=True, slots=True)
class BoundedResponse:
    status_code: int
    headers: httpx.Headers
    content: bytes

    def raise_for_status(self, url: str) -> None:
        if not 200 <= self.status_code < 300:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code} for {url}",
                request=httpx.Request("GET", url),
                response=httpx.Response(self.status_code),
            )


def get_bounded(client: httpx.Client, url: str, *, max_bytes: int, **kwargs: Any) -> BoundedResponse:
    """GET ``url`` reading at most ``max_bytes`` of decoded body; raise ResponseTooLarge beyond."""
    with client.stream("GET", url, **kwargs) as resp:
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > max_bytes:
                raise ResponseTooLarge(url, max_bytes)
            chunks.append(chunk)
        return BoundedResponse(resp.status_code, resp.headers, b"".join(chunks))
