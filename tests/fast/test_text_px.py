"""The official price when a long text went out (owner 2026-11-18): a measurement beside the booked fill."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.fast import long_book as lb, text_px as tp
from sgt.fast import patterns as pt
from sgt.ops.store import OpsStore

ENTRY = pd.Timestamp("2026-11-09 15:03", tz="UTC")               # the fill bar
SENT = pd.Timestamp("2026-11-09 15:04:12", tz="UTC")             # the text: 72 s after the booked price
NIGHT = pd.Timestamp("2026-11-10 01:40", tz="UTC").to_pydatetime()
NS = 1_000_000_000


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def prints(rows, base=SENT.floor("min")):
    """(seconds into the 15:04 minute, price) -> cleaned rows (ns, price, size)."""
    return np.array([(base.value + s * NS, p, 100) for s, p in rows], float).reshape(-1, 3)


def bars(rows, start=ENTRY):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": 100}
                         for i, (o, h, lo, c) in enumerate(rows)])


def long_row(db, status="settled", created=SENT, entry=10.0, stop=9.7, target=10.6, sym="ABCD", session="2026-11-09", fid=1):
    with db.tx() as c:
        return c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, target, entry_ts, est_entry, "
                         "entry, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (fid, sym, session, "L4a", status, stop, target, ENTRY.isoformat(), entry, entry,
                          created.isoformat())).lastrowid


def row(db, table="long_trades"):
    with db.tx() as c:
        return [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")]


FLAT = bars([(10.0, 10.1, 9.9, 10.0)] * 2 + [(10.2, 10.3, 10.1, 10.2)] * 40)     # 15:03, 15:04, then 15:05 on


def test_first_print_at_or_after_the_text_within_two_minutes():
    tr = prints([(5, 9.9), (12, 10.3), (30, 10.4), (70, 10.5)])
    ts, px, rest = tp.first_print(tr, SENT.to_pydatetime())
    assert px == 10.3 and ts == SENT and [p for p in rest[:, 1]] == [10.4]        # 15:05:10 is another minute
    assert tp.first_print(prints([(5, 9.9)]), SENT.to_pydatetime()) is None
    assert tp.first_print(prints([(12 + 121, 10.3)]), SENT.to_pydatetime()) is None
    assert tp.first_print(None, SENT.to_pydatetime()) is None


def test_outcome_stop_target_time_and_no_trade():
    none = np.empty((0, 3))
    assert tp.outcome(9.7, none, FLAT, 9.7, 10.6) == (tp.THROUGH_STOP, None)
    assert tp.outcome(10.6, none, FLAT, 9.7, 10.6) == (tp.PAST_TARGET, None)
    how, g = tp.outcome(10.3, prints([(20, 10.2), (40, 9.65)]), FLAT, 9.7, 10.6)   # stopped inside the text's minute
    assert how == "stop" and g == pytest.approx(9.7 / 10.3 - 1)
    how, g = tp.outcome(10.3, prints([(20, 10.7)]), FLAT, 9.7, 10.6)
    assert how == "target" and g == pytest.approx(10.6 / 10.3 - 1)
    gap = bars([(9.5, 9.6, 9.4, 9.5)])                                              # a later bar opens below the stop
    how, g = tp.outcome(10.3, none, gap, 9.7, 10.6)
    assert how == "stop" and g == pytest.approx(9.5 / 10.3 - 1)
    how, g = tp.outcome(10.3, none, FLAT, 9.7, 10.6)
    assert how == "time" and g == pytest.approx(10.2 / 10.3 - 1)


def test_run_prices_the_text_and_what_buying_there_made(db):
    long_row(db)
    asked = []

    def feed(sym, start, end):
        asked.append((sym, pd.Timestamp(start), pd.Timestamp(end)))
        return prints([(5, 9.9), (12.5, 10.3), (40, 10.25)])
    out = tp.run(db, feed, lambda s, d: FLAT, now=NIGHT)
    (r,) = row(db)
    assert (r["text_px"], r["text_how"]) == (10.3, "time") and r["text_gross"] == pytest.approx(10.2 / 10.3 - 1)
    assert pd.Timestamp(r["text_px_at"]) == SENT + pd.Timedelta(seconds=0.5)
    assert asked == [("ABCD", SENT, pd.Timestamp("2026-11-09 15:07", tz="UTC"))]
    assert out == ["ABCD text $10.30 vs booked $10.00 (+3.0%; time)"]
    assert tp.run(db, feed, lambda s, d: FLAT, now=NIGHT) == [] and len(asked) == 1     # written once
    assert tp.line(db, "2026-11-09") == ("price when each long text went out (official): ABCD $10.30 vs booked $10.00 "
                                          "(+3.0%; from there time -1.0%)")
    assert tp.line(db, "2026-11-10") is None


def test_a_failed_request_is_retried_and_no_print_is_final(db):
    long_row(db)
    assert tp.run(db, lambda *a: None, lambda s, d: FLAT, now=NIGHT) == [] and row(db)[0]["text_how"] is None
    assert tp.run(db, lambda *a: np.empty((0, 3)), lambda s, d: FLAT, now=NIGHT) == []
    assert row(db)[0]["text_how"] == tp.NO_PRINT and row(db)[0]["text_px"] is None


def test_rows_not_priced(db):
    long_row(db, status="open", fid=1)                                         # not final yet
    long_row(db, status="missed", fid=2)                                       # never texted
    long_row(db, created=ENTRY + pd.Timedelta(minutes=66), fid=3)              # booked after a restart
    long_row(db, session="2026-10-01", fid=4)                                  # older than the lookback
    calls = []
    tp.run(db, lambda *a: calls.append(a) or prints([(13, 10.3)]), lambda s, d: FLAT, now=NIGHT)
    got = {r["fade_id"]: r["text_how"] for r in row(db)}
    assert got == {1: None, 2: None, 3: tp.NOT_LIVE, 4: None} and calls == []


def test_seen_rows_are_priced_and_nothing_before_the_official_data_is_complete(db):
    long_row(db, status=lb.SEEN)
    early = pd.Timestamp("2026-11-09 21:10", tz="UTC").to_pydatetime()         # 16:10 ET: within 20 min of the close
    assert tp.run(db, lambda *a: prints([(13, 10.3)]), lambda s, d: FLAT, now=early) == []
    assert tp.run(db, lambda *a: prints([(13, 10.3)]), lambda s, d: FLAT, now=NIGHT) != []
    assert row(db)[0]["status"] == lb.SEEN and row(db)[0]["text_px"] == 10.3


def test_armed_text_price_and_the_digest_line(db):
    long_row(db)
    with db.tx() as c:
        c.execute("INSERT INTO long_armed(fade_id, symbol, session, ready, texted_at, created_at) VALUES (1,'ABCD',"
                  "'2026-11-09','2026-11-09T15:03:00+00:00','2026-11-09T15:03:08+00:00','x')")
        c.execute("INSERT INTO long_armed(fade_id, symbol, session, ready, note, created_at) VALUES (1,'ABCD',"
                  "'2026-11-09','2026-11-09T15:09:00+00:00','seen late','x')")

    def feed(sym, start, end):
        return prints([(9, 10.02)], base=ENTRY) if pd.Timestamp(start) < SENT else prints([(13, 10.3)])
    out = tp.run(db, feed, lambda s, d: FLAT, now=NIGHT)
    a = row(db, "long_armed")
    assert (a[0]["px"], a[0]["px_note"]) == (10.02, "ok") and a[1]["px"] is None and a[1]["px_note"] is None
    assert "ABCD armed text $10.02" in out and len(out) == 2
    assert tp.line(db, "2026-11-09").endswith("(+3.0%; from there time -1.0%, armed text $10.02)")


def test_one_bad_row_never_blocks_the_others(db):
    long_row(db, sym="BAD", fid=1)
    long_row(db, sym="GOOD", fid=2)

    def feed(sym, start, end):
        if sym == "BAD":
            raise RuntimeError("boom")
        return prints([(13, 10.3)])
    assert tp.run(db, feed, lambda s, d: FLAT, now=NIGHT) == ["GOOD text $10.30 vs booked $10.00 (+3.0%; time)"]


# --- the listener's official-print fetch (external bytes) ------------------------------------------------------------

def _prints_fn(handler):
    import sys
    from pathlib import Path

    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fade_live as fl
    return fl, fl.official_prints(httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)))


def test_official_prints_parse_strictly_and_stay_bounded():
    import httpx

    seen = []

    def handler(req):
        seen.append(dict(req.url.params))
        page = len(seen)
        rows = [{"t": "2026-11-09T15:04:12.5Z", "p": 10.3, "s": 200, "c": ["@"]},
                {"t": "2026-11-09T15:04:13Z", "p": 10.4, "s": 50, "c": ["I"]},        # odd lot: not a chart price
                {"t": "bad", "p": "x"}, "junk", {"t": "2026-11-09T15:04:14Z", "p": -1, "s": 10}]
        return httpx.Response(200, json={"trades": rows, "next_page_token": f"tok{page}"})
    fl, prints = _prints_fn(handler)
    tr = prints("ABCD", SENT.to_pydatetime(), (SENT + pd.Timedelta(minutes=2)).to_pydatetime())
    assert len(seen) == fl.PRINT_PAGES and seen[0]["feed"] == "sip" and seen[1]["page_token"] == "tok1"
    assert tr.shape == (fl.PRINT_PAGES, 3) and set(tr[:, 1]) == {10.3}
    assert tp.first_print(tr, SENT.to_pydatetime())[1] == 10.3


@pytest.mark.parametrize("resp", [lambda: __import__("httpx").Response(403, json={"message": "recent data"}),
                                  lambda: __import__("httpx").Response(200, text="not json"),
                                  lambda: __import__("httpx").Response(200, json={"trades": {"ABCD": []}}),
                                  lambda: __import__("httpx").Response(200, json=[1, 2])])
def test_official_prints_fail_closed(resp):
    _, prints = _prints_fn(lambda req: resp())
    assert prints("ABCD", SENT.to_pydatetime(), SENT.to_pydatetime()) is None


def test_official_prints_empty_span_is_not_a_failure():
    import httpx

    _, prints = _prints_fn(lambda req: httpx.Response(200, json={"trades": None, "next_page_token": None}))
    assert prints("ABCD", SENT.to_pydatetime(), SENT.to_pydatetime()).shape == (0, 3)


def test_a_price_is_written_once(db):
    rid = long_row(db)
    assert tp._put(db, "long_trades", rid, "text_how", text_px=10.3, text_how="time")
    assert not tp._put(db, "long_trades", rid, "text_how", text_px=99.0, text_how="stop")
    assert (row(db)[0]["text_px"], row(db)[0]["text_how"]) == (10.3, "time")


def test_the_digest_shows_the_text_price_line_on_a_measure_only_day(db):
    from datetime import date

    from sgt.ops import digest

    long_row(db, status=lb.SEEN)
    db.set_flag(lb.OFF_FLAG, "1")
    tp.run(db, lambda *a: prints([(13, 10.3)]), lambda s, d: FLAT, now=NIGHT)
    lines, usd = digest.long_today(db, date(2026, 11, 9))
    assert usd == 0.0 and any("price when each long text went out (official): ABCD $10.30 vs booked $10.00" in x for x in lines)
    assert not any("price when each long text" in x for x in digest.long_today(db, date(2026, 11, 10))[0])
