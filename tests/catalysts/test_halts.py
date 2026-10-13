"""Nasdaq halt feed parsing, bounds, and idempotent collection.

The fixture field names follow the published Nasdaq Trader trade-halt RSS and
should be re-verified against a live sample before production use.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx

from sgt.catalysts.halts import HALTS_URL, HaltsCollector, load_halts, parse_halts
from sgt.core.types import HaltState

UTC = timezone.utc
FIXTURE = Path(__file__).parent / "fixtures" / "nasdaq_halts.xml"


def _payload() -> bytes:
    return FIXTURE.read_bytes()


def test_parse_halt_and_resume():
    events = parse_halts(_payload())
    wff = [e for e in events if e.symbol == "WFF"]
    assert {e.state for e in wff} == {HaltState.HALTED, HaltState.RESUMED}
    halted = next(e for e in wff if e.state is HaltState.HALTED)
    resumed = next(e for e in wff if e.state is HaltState.RESUMED)
    # 2026-10-12 is EDT (UTC-4): 13:09 ET -> 17:09 UTC; 13:14:30 ET -> 17:14:30 UTC
    assert halted.ts == datetime(2026, 10, 12, 17, 9, 0, tzinfo=UTC)
    assert resumed.ts == datetime(2026, 10, 12, 17, 14, 30, tzinfo=UTC)
    assert halted.reason_code == "LUDP"


def test_blank_resumption_yields_only_halt():
    events = parse_halts(_payload())
    ymat = [e for e in events if e.symbol == "YMAT"]
    assert len(ymat) == 1 and ymat[0].state is HaltState.HALTED
    assert ymat[0].ts == datetime(2026, 10, 12, 13, 45, 12, tzinfo=UTC)  # 09:45:12 EDT


def test_oversized_payload_rejected():
    with pytest.raises(ValueError, match="too large"):
        parse_halts(b"<rss>" + b"x" * (5 * 1024 * 1024) + b"</rss>")


def test_dtd_rejected():
    payload = b'<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY a "b">]><rss></rss>'
    with pytest.raises(ValueError, match="DTD"):
        parse_halts(payload)


def test_malformed_xml_rejected():
    with pytest.raises(ValueError, match="well-formed"):
        parse_halts(b"<rss><item>")


@respx.mock
def test_collect_is_idempotent():
    respx.get(HALTS_URL).mock(return_value=httpx.Response(200, content=_payload()))
    c = HaltsCollector(client=httpx.Client())
    n1 = c.collect()
    n2 = c.collect()
    assert n1 == n2 == 3  # WFF halt+resume, YMAT halt

    events = load_halts("WFF", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC))
    assert len(events) == 2
    assert [e.state for e in events] == [HaltState.HALTED, HaltState.RESUMED]  # time-ordered


@respx.mock
def test_load_halts_filters_symbol_and_range():
    respx.get(HALTS_URL).mock(return_value=httpx.Response(200, content=_payload()))
    HaltsCollector(client=httpx.Client()).collect()
    assert load_halts("YMAT", datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC))
    assert load_halts("WFF", datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 12, 31, tzinfo=UTC)) == []
