from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

from sgt.fast import fade, filings

AT = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


def sub(rows):
    return {"filings": {"recent": {"form": [f for f, _ in rows], "acceptanceDateTime": [a for _, a in rows]}}}


def test_flags_are_point_in_time_and_name_the_latest_offering():
    s = sub([("8-K", "2026-11-05T14:00:00.000Z"), ("424B5", "2026-11-02T21:05:00.000Z"),
             ("S-1", "2026-09-01T12:00:00.000Z"),                       # older than 30 days
             ("424B4", "2026-11-05T15:30:00.000Z"),                     # accepted AFTER the alert: ignored
             ("10-Q", "2026-10-20T20:00:00.000Z"), ("<script>", "2026-11-04T10:00:00.000Z")])
    f = filings.flags(s, AT)
    assert f["dilution_30d"] == 1 and f["dilution_form"] == "424B5" and f["dilution_days"] == 2.7
    assert f["eightk_today"] == 1 and f["forms_30d"] == "8-K,424B5,10-Q"


def test_no_filings_and_malformed_payloads_are_safe():
    assert filings.flags({}, AT) == {"dilution_30d": 0, "dilution_form": None, "dilution_days": None,
                                     "eightk_today": 0, "forms_30d": None}
    assert filings.flags(sub([("8-K", "not a date")]), AT)["eightk_today"] == 0
    assert filings.flags({"filings": {"recent": {"form": None}}}, AT)["forms_30d"] is None


def test_lookup_without_sec_contact_is_unknown_not_clean(monkeypatch):
    monkeypatch.setattr(filings, "get_secret", lambda name, required=True: None)
    assert filings.lookup("ABCD", AT) is None
    assert filings.lookup("bad symbol;", AT) is None


def test_lookup_resolves_cik_and_bounds_the_response(monkeypatch, tmp_path):
    monkeypatch.setattr(filings, "get_secret", lambda name, required=True: "owner@example.com")
    monkeypatch.setattr(filings, "TICKER_CACHE", tmp_path / "t.json")
    big = {"x": "y" * (filings.MAX_BYTES + 10)}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"ticker": "ABCD", "cik_str": 123}, "1": {"ticker": "HUGE", "cik_str": 9}})
        if req.url.path.endswith("CIK0000000123.json"):
            return httpx.Response(200, json=sub([("S-3", "2026-11-04T12:00:00.000Z")]))
        return httpx.Response(200, content=json.dumps(big).encode())

    c = httpx.Client(transport=httpx.MockTransport(handler))
    f = filings.lookup("ABCD", AT, client=c)
    assert f["dilution_30d"] == 1 and f["dilution_form"] == "S-3"
    assert filings.lookup("HUGE", AT, client=c) is None                  # over MAX_BYTES -> unknown
    assert filings.lookup("ZZZZ", AT, client=c) is None                  # not an SEC registrant -> unknown
    assert json.loads((tmp_path / "t.json").read_text())["ABCD"] == 123  # cached for the day


def test_entry_text_shows_offering_and_omits_unknown():
    ctx = {"news": {"news_any": 0, "news_own": 0, "news_earn": 0}}
    base = fade.ask_text("ABCD", "volume_spike", 4.0, 5000, ctx, "")
    assert "SEC" not in base                                             # unknown: no claim either way
    t = fade.ask_text("ABCD", "volume_spike", 4.0, 5000, {**ctx, "filings": {
        "dilution_30d": 1, "dilution_form": "424B5", "dilution_days": 2.7, "eightk_today": 1}}, "")
    assert "offering filed (424B5, 3d ago)" in t and "8-K today" in t
    t = fade.ask_text("ABCD", "volume_spike", 4.0, 5000, {**ctx, "filings": {
        "dilution_30d": 0, "dilution_form": None, "dilution_days": None, "eightk_today": 0}}, "")
    assert "no offering filed in 30 days" in t


def test_lookback_is_thirty_days():
    assert filings.LOOKBACK == timedelta(days=30)
