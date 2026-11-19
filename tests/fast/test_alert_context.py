"""The free sources are read for EVERY alert at alert time (owner 2026-11-19), and never disturb the short book."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from sgt.fast import alert_context as ac, fade, news
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

T10 = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)          # 10:00 ET, a session
ALERT_AT = T10 - timedelta(seconds=10)
NEWS = {"news_own": True, "news_earn": False, "headline": "ABC prices $5M registered direct offering", "news_kind": "OFFERING"}
QUIET = {"news_own": False, "news_earn": False, "headline": None, "news_kind": "NONE"}
FILING = {"dilution_30d": True, "dilution_form": "S-3", "dilution_days": 4.0, "eightk_today": True}
SOCIAL = {"st_n60": 41, "st_n15": 12, "st_span_min": 33.0, "st_bull": 9, "st_bear": 2, "st_watch": 1800}
REDDIT = {"rd_rank": 7, "rd_mentions": 120, "rd_upvotes": 900, "rd_mentions_prev": 15}


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def gather(jobs, budget):
    out = {}
    for k, f in jobs.items():
        try:
            out[k] = f()
        except Exception:  # noqa: BLE001 - as the live gather: a failed source is unknown
            out[k] = None
    return out


def looks(**over):
    base = {"news": lambda s, at: NEWS, "filings": lambda s, at: FILING, "social": lambda s, at: SOCIAL,
            "reddit": lambda s, at: REDDIT}
    return {**base, **over}


def put(db, sym, status="pending", reason=None, posted=ALERT_AT, typ="volume_spike"):
    with db.tx() as c:
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                         " reason, weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (sym, "2026-11-09", typ, posted.isoformat(), "-1", abs(hash(sym)) % 100_000, status, reason,
                          0.05, posted.isoformat())).lastrowid


def row(db, rid):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM fade_trades WHERE id=?", (rid,)).fetchone())


# ---- the news kind --------------------------------------------------------------------------------------------------

def test_the_kind_is_the_first_pattern_in_the_frozen_order():
    assert news.kind_of([]) == "NONE"
    assert news.kind_of(["ABC announces pricing of public offering", "ABC Q3 results"]) == "OFFERING"
    assert news.kind_of(["ABC Q3 revenue beats"]) == "EARNINGS"
    assert news.kind_of(["ABC gets FDA clearance"]) == "CLINICAL"
    assert news.kind_of(["ABC signs supply agreement"]) == "DEAL"
    assert news.kind_of(["Analyst initiates ABC with buy"]) == "ANALYST"
    assert news.kind_of(["ABC names new chief executive", None]) == "OTHER"
    assert news.kind_of(["x"] * news.MAX_KIND_HEADLINES + ["ABC offering"]) == "OTHER"      # bounded read
    assert set(news.KINDS) == set(ac.KIND_WORDS)


def test_the_live_kinds_are_the_stored_studies_patterns():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import gap_news_study as g

    assert [(k, p.pattern, p.flags) for k, p in news.KIND_PATTERNS] == [(k, p.pattern, p.flags) for k, p in g.PATTERNS]
    for hs in ([], ["ABC warrant exercise"], ["ABC phase 2 topline"], ["ABC appoints director"]):
        assert news.kind_of(hs) == g.kind_of(hs)


def test_flags_carry_the_kind_of_the_companys_own_headlines_only():
    f = pd.DataFrame([("2026-11-09T13:00:00Z", "ABC", "ABC prices offering"),
                      ("2026-11-09T13:05:00Z", "ABC,B,C,D", "Movers: ABC gets FDA approval")],
                     columns=["created_at", "symbols", "headline"])
    assert news.flags("ABC", ALERT_AT, f)["news_kind"] == "OFFERING"
    assert news.flags("ABC", ALERT_AT, f.iloc[1:])["news_kind"] == "NONE"       # a roundup is not company news


# ---- the pass -------------------------------------------------------------------------------------------------------

def test_every_alert_is_read_as_of_the_alert_whatever_the_short_book_did_with_it(db):
    ids = [put(db, "PEND"), put(db, "FILT", "filtered", "outside 09:30-15:44"), put(db, "SKIP", "skipped", "alert received late")]
    seen = []
    n = ac.stamp(db, looks(news=lambda s, at: (seen.append((s, at)), NEWS)[1]), gather, now=T10 + timedelta(seconds=20))
    assert n == 3 and seen == [(s, ALERT_AT) for s in ("PEND", "FILT", "SKIP")]      # at = the alert, not the clock
    for rid in ids:
        r = row(db, rid)
        assert (r["ctx_news_own"], r["ctx_news_earn"], r["news_kind"], r["ctx_head"]) == (1, 0, "OFFERING", NEWS["headline"])
        assert (r["filing_dilution"], r["filing_form"], r["filing_8k"]) == (1, "S-3", 1)
        assert (r["st_n60"], r["st_n15"], r["st_bull"], r["st_bear"], r["rd_rank"], r["rd_mentions"]) == (41, 12, 9, 2, 7, 120)
        assert r["ctx_at"] == (T10 + timedelta(seconds=20)).isoformat() and r["ctx_note"] == "read: news, filings, social, reddit"
        assert r["x_n60"] is None                                # X costs money per lookup: never read here
    assert ac.stamp(db, looks(), gather, now=T10 + timedelta(seconds=30)) == 0       # once per alert


def test_x_is_not_a_source_here():
    assert "x" not in ac.SOURCES and set(ac.SOURCES) == {"news", "filings", "social", "reddit"}


def test_the_news_gates_own_columns_are_never_written(db):
    """news_own IS NULL is how fade.monitor knows the gate has not judged an alert: this read must leave it NULL."""
    rid = put(db, "PEND")
    ac.stamp(db, looks(), gather, now=T10)
    r = row(db, rid)
    assert (r["news_own"], r["news_earn"], r["news_head"]) == (None, None, None) and r["ctx_news_own"] == 1
    assert not {"news_own", "news_earn", "news_head"} & set(ac.columns({"news": NEWS, "filings": FILING, "social": SOCIAL, "reddit": REDDIT}))


@pytest.mark.parametrize("read,gate,status", [(QUIET, NEWS, "filtered"), (NEWS, QUIET, "open"), (NEWS, NEWS, "filtered")])
def test_the_short_books_news_gate_still_judges_an_alert_this_pass_read_first(db, read, gate, status):
    ap.arm(db, T10)
    assert fade.on_alert(db, fade.LiveAlert("ABC", "volume_spike", ALERT_AT, "-1", 1, None), now=T10).startswith("pending")
    assert ac.stamp(db, looks(news=lambda s, at: read), gather, now=T10 + timedelta(seconds=2)) == 1
    asked = []
    fade.monitor(db, [].append, lambda s: 10.0, now=T10 + timedelta(seconds=5),
                 news=lambda s, at: (asked.append(s), gate)[1])
    (r,) = [row(db, 1)]
    assert asked == ["ABC"] and r["status"] == status            # the gate looked for itself and decided by ITS read
    assert r["news_own"] == int(gate["news_own"]) and r["ctx_news_own"] == int(read["news_own"])
    assert (r["reason"] == fade.NEWS_REASON) == (status == "filtered")


def test_a_column_another_path_stamped_is_kept(db):
    rid = put(db, "HAS")
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET st_n60=3, news_kind='DEAL' WHERE id=?", (rid,))
    ac.stamp(db, looks(), gather, now=T10)
    r = row(db, rid)
    assert (r["st_n60"], r["news_kind"]) == (3, "DEAL") and r["st_n15"] == 12 and r["ctx_at"]


def test_unknown_is_unknown_and_a_dead_pass_is_retried_while_the_alert_is_fresh(db):
    rid = put(db, "DOWN")
    dead = {k: (lambda s, at: None) for k in ac.SOURCES}
    assert ac.stamp(db, dead, gather, now=T10) == 0 and row(db, rid)["ctx_at"] is None
    boom = looks(news=lambda s, at: (_ for _ in ()).throw(RuntimeError("feed down")), reddit=lambda s, at: None)
    assert ac.stamp(db, boom, gather, now=T10 + timedelta(seconds=10)) == 1
    r = row(db, rid)
    assert r["ctx_note"] == "read: filings, social; no answer: news, reddit"
    assert (r["ctx_news_own"], r["news_kind"], r["rd_rank"]) == (None, None, None) and r["st_n60"] == 41
    assert ac.case(r) == "news unknown" and "news" not in ac.words(r)


def test_only_fresh_alerts_and_a_bounded_number_per_pass(db):
    old = put(db, "OLD", posted=T10 - ac.FRESH - timedelta(seconds=1))
    edge = put(db, "EDGE", posted=T10 - ac.FRESH)
    more = [put(db, f"N{i}") for i in range(ac.MAX_PER_PASS + 1)]
    calls = []
    lk = looks(social=lambda s, at: (calls.append(s), SOCIAL)[1])
    assert ac.stamp(db, lk, gather, now=T10) == ac.MAX_PER_PASS and calls == ["EDGE", "N0", "N1"]
    assert ac.stamp(db, lk, gather, now=T10) == 2 and row(db, more[-1])["ctx_at"]
    assert row(db, old)["ctx_at"] is None and row(db, edge)["ctx_at"]         # too old: the read would be of a later time


def test_one_alert_failing_does_not_stop_the_others(db):
    a, b = put(db, "BAD"), put(db, "GOOD")

    seen = []

    def one(jobs, budget):
        res = gather(jobs, budget)
        seen.append(1)
        if len(seen) == 1:
            raise RuntimeError("pool broke")
        return res
    assert ac.stamp(db, looks(), one, now=T10) == 1
    assert row(db, a)["ctx_at"] is None and row(db, b)["ctx_at"]


def test_a_row_stamped_meanwhile_is_not_stamped_again(db):
    rid = put(db, "RACE")

    def slow(jobs, budget):
        res = gather(jobs, budget)
        with db.tx() as c:                                       # another pass finished this alert while we were reading
            c.execute("UPDATE fade_trades SET ctx_at='earlier', ctx_note='theirs', st_n15=99 WHERE id=?", (rid,))
        return res
    assert ac.stamp(db, looks(), slow, now=T10) == 0
    r = row(db, rid)
    assert (r["ctx_at"], r["ctx_note"], r["st_n15"], r["st_n60"]) == ("earlier", "theirs", 99, None)


# ---- readers --------------------------------------------------------------------------------------------------------

def test_the_case_prefers_the_gates_stamp_and_words_are_one_short_line(db):
    rid = put(db, "ABC")
    ac.stamp(db, looks(), gather, now=T10)
    r = row(db, rid)
    assert ac.case(r) == "company news"
    assert ac.words(r) == "news: offering · offering filed (S-3) · 8-K today · StockTwits 41/hr · Reddit #7"
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET news_own=0, news_earn=0 WHERE id=?", (rid,))    # the gate judged: no news
    r = row(db, rid)
    assert ac.case(r) == "no company news" and ac.words(r).startswith("no company news · offering filed")
    assert ac.line(db, rid).startswith("\n\U0001F4F0 At the alert: no company news")
    bare = put(db, "BARE")
    assert ac.case(row(db, bare)) == "news unknown" and ac.words(row(db, bare)) == "" and ac.line(db, bare) == ""
    assert ac.line(db, 99_999) == ""


def test_longs_are_scored_by_case_in_the_nightly_summary(db):
    assert ac.long_cases_line(db) is None
    spec = [("A", 1, 0.04), ("B", 1, -0.02), ("C", 0, -0.03), ("D", None, 0.01), ("E", 0, None)]
    for sym, own, net in spec:
        fid = put(db, sym)
        with db.tx() as c:
            c.execute("UPDATE fade_trades SET ctx_news_own=?, ctx_news_earn=? WHERE id=?", (own, None if own is None else 0, fid))
            c.execute("INSERT INTO long_trades(fade_id, symbol, session, status, net, created_at) VALUES (?,?,?,?,?,?)",
                      (fid, sym, "2026-11-09", "settled" if net is not None else "open", net, "x"))
    assert ac.long_cases_line(db) == ("longs by case: company news 2 avg +1.00% · no company news 1 avg -3.00% · "
                                      "news unknown 1 avg +1.00%")
