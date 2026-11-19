"""The free sources read for EVERY alert, at alert time (owner 2026-11-19: "connect to reddit and news articles and
sources").

Before this module the connectors ran only inside the short book's entry text: company news was stamped on alerts
that reached the news gate, and SEC filings, StockTwits and Reddit only on a short that was entered (6 of 188 alerts
from 2026-11-12 to 2026-11-19). The long book used none of them. Here every alert row gets, within minutes of the
alert: company news before the alert and its kind (Alpaca / Benzinga), SEC filings (EDGAR: an offering filed in 30
days, an 8-K today), StockTwits message counts and Reddit mentions (ApeWisdom). X is NOT read here: it costs money
per lookup and stays on the short book's entry path (owner rule: no new spending).

A read for the record and for scoring trades by their case. It gates nothing and sizes nothing: a source that does
not answer is unknown (NULL), never "no news". It never overwrites a column another path already stamped (the short's
entry text), and it never writes ``news_own`` / ``news_earn``: NULL there is how the short book's news gate (fade F6,
``fade.monitor``) knows it has not judged an alert yet, so the news read here lands in ``ctx_news_own``,
``ctx_news_earn`` and ``ctx_head``. Readers (``case``, ``words``) prefer the gate's own stamp when there is one. Bounded: a few alerts a pass, one wall-clock budget per alert, only alerts young enough
that the read still describes the moment of the alert."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
SOURCES = ("news", "filings", "social", "reddit")      # free; X (paid per lookup) is not in this list on purpose
FRESH = timedelta(minutes=10)           # an alert older than this is not stamped: the read would describe a later time
MAX_PER_PASS = 3                        # alerts read in one pass (each: len(SOURCES) requests in parallel)
BUDGET = 8.0                            # seconds for one alert's sources together; a late one is unknown
Lookup = Callable[[str, datetime], dict | None]         # (symbol, at) -> the source's dict, None = unknown
Gather = Callable[[dict, float], dict]                  # ({name: job}, budget seconds) -> {name: result or None}
KIND_WORDS = {"OFFERING": "offering", "EARNINGS": "earnings", "CLINICAL": "clinical / FDA", "DEAL": "deal / contract",
              "ANALYST": "analyst", "OTHER": "other news", "NONE": "no company news"}
CASES = ("company news", "no company news", "news unknown")


def columns(ctx: dict) -> dict:
    """fade_trades columns from the sources' results; a source that is None (unknown) contributes nothing. Filings,
    StockTwits and Reddit use the names and meanings the short's entry text stores (sgt.fast.fade._context); the news
    read has its own columns (module doc)."""
    cols: dict = {}
    n = ctx.get("news")
    if n:
        cols.update(ctx_news_own=int(bool(n.get("news_own"))), ctx_news_earn=int(bool(n.get("news_earn"))),
                    ctx_head=n.get("headline"), news_kind=n.get("news_kind"))
    fl = ctx.get("filings")
    if fl:
        cols.update(filing_dilution=int(bool(fl.get("dilution_30d"))), filing_form=fl.get("dilution_form"),
                    filing_8k=int(bool(fl.get("eightk_today"))))
    st = ctx.get("social")
    if st:
        cols.update(st_n60=st.get("st_n60"), st_n15=st.get("st_n15"), st_span=st.get("st_span_min"),
                    st_bull=st.get("st_bull"), st_bear=st.get("st_bear"), st_watch=st.get("st_watch"))
    rd = ctx.get("reddit")
    if rd:
        cols.update({k: rd.get(k) for k in ("rd_rank", "rd_mentions", "rd_upvotes", "rd_mentions_prev")})
    return cols


def due(db: OpsStore, now: datetime) -> list[dict]:
    """Alert rows not yet read and still fresh, oldest first, at most MAX_PER_PASS. Every status counts: an alert the
    short book filtered is still an alert the long book may trade and the site lists."""
    with db.tx() as c:
        rows = c.execute("SELECT id, symbol, posted_at FROM fade_trades WHERE ctx_at IS NULL AND posted_at>=? "
                         "ORDER BY id LIMIT ?", (iso(now - FRESH), MAX_PER_PASS)).fetchall()
    return [dict(r) for r in rows]


def stamp(db: OpsStore, lookups: dict[str, Lookup], gather: Gather, now: datetime | None = None) -> int:
    """One pass: read the sources for each due alert AS OF THE ALERT and store what answered. Returns the number of
    alerts stamped. An alert whose sources all failed is left for the next pass (while it is fresh); one alert failing
    never stops the others."""
    now = now or utcnow()
    done = 0
    for r in due(db, now):
        try:
            at = datetime.fromisoformat(r["posted_at"])
            jobs = {k: (lambda f=lookups[k]: f(r["symbol"], at)) for k in SOURCES if k in lookups}
            res = gather(jobs, BUDGET) if jobs else {}
            got = [k for k in SOURCES if res.get(k)]
            if not got:
                continue                                   # nothing answered: unknown, retried while fresh
            cols = columns(res)
            missing = [k for k in SOURCES if k not in got]
            note = "read: " + ", ".join(got) + ("; no answer: " + ", ".join(missing) if missing else "")
            sets = ", ".join([f"{k}=COALESCE({k}, ?)" for k in cols] + ["ctx_at=?", "ctx_note=?"])   # never overwrite
            with db.tx() as c:                                               # another path's stamp
                n = c.execute(f"UPDATE fade_trades SET {sets} WHERE id=? AND ctx_at IS NULL",  # noqa: S608 - fixed names
                              (*cols.values(), iso(now), note, r["id"])).rowcount
            done += n
        except Exception:  # noqa: BLE001 - a read for the record: one alert failing never stops the pass; logged
            log.exception("alert context for %s failed", r["id"])
    return done


def _news(row) -> bool | None:
    """Company news before the alert: the short book's own stamp when it judged the alert, else this module's read;
    None = unknown."""
    for own, earn in ((row["news_own"], row["news_earn"]), (row["ctx_news_own"], row["ctx_news_earn"])):
        if own is not None or earn is not None:
            return bool(own or earn)
    return None


def case(row) -> str:
    """The news case of an alert row, for scoring trades by case: 'company news', 'no company news', 'news unknown'."""
    n = _news(row)
    return CASES[2] if n is None else (CASES[0] if n else CASES[1])


def words(row) -> str:
    """One short line of what the sources said at the alert, for a text or the site; "" when nothing was read."""
    bits = []
    n, kind = _news(row), row["news_kind"]
    if n is not None:
        bits.append(f"news: {KIND_WORDS.get(kind, 'yes') if kind != 'NONE' else 'yes'}" if n else "no company news")
    if row["filing_dilution"]:
        bits.append("offering filed" + (f" ({str(row['filing_form'])[:16]})" if row["filing_form"] else ""))
    if row["filing_8k"]:
        bits.append("8-K today")
    if row["st_n60"] is not None:
        bits.append(f"StockTwits {int(row['st_n60'])}/hr")
    if row["rd_rank"] is not None:
        bits.append(f"Reddit #{int(row['rd_rank'])}")
    return " · ".join(bits)


CTX_COLS = ("news_own, news_earn, ctx_news_own, ctx_news_earn, news_kind, filing_dilution, filing_form, filing_8k, "
            "st_n60, rd_rank")


def line(db: OpsStore, fade_id: int) -> str:
    """The context line of one alert for a long text ("" when unknown). Never raises: an extra line."""
    try:
        with db.tx() as c:
            r = c.execute(f"SELECT {CTX_COLS} FROM fade_trades WHERE id=?", (fade_id,)).fetchone()  # noqa: S608 - fixed names
        w = words(r) if r is not None else ""
        return f"\n\U0001F4F0 At the alert: {w}" if w else ""
    except Exception:  # noqa: BLE001 - an extra line; the text itself must go out
        log.exception("alert context line failed")
        return ""


def long_cases_line(db: OpsStore) -> str | None:
    """Settled live longs by the news case of their alert: trades and average official result per case. A measurement
    for the nightly summary; None before any long has settled."""
    with db.tx() as c:
        rows = c.execute("SELECT t.net, f.news_own, f.news_earn, f.ctx_news_own, f.ctx_news_earn FROM long_trades t "
                         "JOIN fade_trades f ON f.id=t.fade_id "
                         "WHERE t.status='settled' AND t.net IS NOT NULL").fetchall()
    if not rows:
        return None
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(case(r), []).append(float(r["net"]))
    return "longs by case: " + " · ".join(f"{k} {len(by[k])} avg {sum(by[k]) / len(by[k]):+.2%}" for k in CASES if k in by)
