"""Point-in-time company news flags for a fade event (prereg_fade_news_2026-11-05).

The same functions label history (local Benzinga archive) and live events (Alpaca news API), so a live flag
means exactly what the study measured. Only articles created strictly before the alert count.
"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

ET = ZoneInfo("America/New_York")
EARN = re.compile(r"earnings|\beps\b|revenue|sales|guidance|outlook|\bq[1-4]\b|quarter|results|\bbeats?\b|"
                  r"\bmiss(es)?\b|\braises?\b|\blowers?\b", re.I)
MAX_OWN_SYMBOLS = 2                     # an article tagging more names is a roundup, not company news
# The kind of a company's news from its headlines: the first pattern, in this order, that any headline matches. The
# same frozen patterns as scripts/gap_news_study.py (prereg_gap_news_2026-11-18), kept equal by a test, so a kind
# stamped live means what the stored studies measured. A rough classifier: a label for scoring, not a rule.
KIND_PATTERNS = (
    ("OFFERING", re.compile(r"offering|\bpriced\b|pricing of|private placement|registered direct|warrant|at-the-market|"
                            r"\bshelf\b|convertible|dilut", re.I)),
    ("EARNINGS", EARN),
    ("CLINICAL", re.compile(r"\bfda\b|phase (?:1|2|3|i{1,3})\b|\btrial|clinical|topline|approval|clearance|patent", re.I)),
    ("DEAL", re.compile(r"merger|acquir|acquisition|buyout|agreement|contract|partnership|collaboration|\baward|\border", re.I)),
    ("ANALYST", re.compile(r"upgrade|downgrade|price target|initiates|reiterates|maintains", re.I)),
)
KINDS = tuple(k for k, _ in KIND_PATTERNS) + ("OTHER", "NONE")
MAX_KIND_HEADLINES = 50                 # headlines read for the kind (the live feed returns at most this many)
HEADLINE_MAX = 90
_CTRL = re.compile(r"[\x00-\x1f\x7f]+")


def clean(headline) -> str:
    """Untrusted feed text -> one short printable line for an SMS."""
    h = _CTRL.sub(" ", str(headline or "")).strip()
    return h if len(h) <= HEADLINE_MAX else h[:HEADLINE_MAX - 1].rstrip() + "…"


def kind_of(headlines) -> str:
    """NONE without a headline; else the first kind (KIND_PATTERNS order) any headline matches; else OTHER.
    Headlines are untrusted feed text: read as data, bounded in number and length."""
    hs = [str(h or "")[:300] for h in list(headlines)[:MAX_KIND_HEADLINES]]
    if not hs:
        return "NONE"
    for name, pat in KIND_PATTERNS:
        if any(pat.search(h) for h in hs):
            return name
    return "OTHER"


def chart_url(symbol: str) -> str:
    """TradingView chart for the symbol (owner request 2026-11-05: a visual for every trade)."""
    return f"https://www.tradingview.com/chart/?symbol={symbol}" if re.fullmatch(r"[A-Z][A-Z0-9.]{0,9}", symbol) else ""


def window_start(posted_at: datetime) -> datetime:
    """16:00 ET of the previous weekday before the alert's session."""
    d = posted_at.astimezone(ET).date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime.combine(d, time(16), ET)


def articles_for(symbol: str, posted_at: datetime, news: pd.DataFrame) -> pd.DataFrame:
    """Articles tagging ``symbol`` created in [window_start, posted_at). ``news`` has created_at (UTC), symbols,
    headline; created_at may be str or Timestamp."""
    if news.empty:
        return news
    ts = pd.to_datetime(news.created_at, utc=True)
    lo, hi = pd.Timestamp(window_start(posted_at)), pd.Timestamp(posted_at)
    m = (ts >= lo) & (ts < hi)
    sub = news[m]
    tagged = sub.symbols.fillna("").map(lambda s: symbol in s.split(","))
    return sub[tagged]


def flags(symbol: str, posted_at: datetime, news: pd.DataFrame) -> dict:
    a = articles_for(symbol, posted_at, news)
    own = a[a.symbols.map(lambda s: len(s.split(",")) <= MAX_OWN_SYMBOLS)] if len(a) else a
    earn = own[own.headline.fillna("").map(lambda h: bool(EARN.search(h)))] if len(own) else own
    latest = own.sort_values("created_at").iloc[-1].headline if len(own) else None
    return {"news_any": bool(len(a)), "news_own": bool(len(own)), "news_earn": bool(len(earn)),
            "news_n": int(len(own)), "headline": latest,
            "news_kind": kind_of(own.headline.fillna("").tolist() if len(own) else [])}
