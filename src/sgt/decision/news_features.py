"""Point-in-time news-catalyst features (2026-10-28).

Headlines (Benzinga via Alpaca, scripts/backfill_news.py) are classified once; a candidate at
bar ``ts`` (bar OPEN, UTC) sees only headlines created before the bar CLOSES, minus a safety
margin for publication latency. Recap headlines ("why X shares are trading higher") are
written AFTER moves and are excluded entirely, as are analyst-rating notes; only
single-symbol headlines are attributed (multi-ticker stories have no clean owner).

These features exist only in research datasets until a LIVE news source feeds the runtime:
a model selecting them fails closed at inference (DecisionModel refuses missing features).
"""
from __future__ import annotations

import glob
import re
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

RECAP = re.compile(
    r"shares are trading|stock is (?:trading|moving|rising|falling)|shares (?:are )?(?:rising|"
    r"falling|moving|soaring|surging|plunging|jumping|down|up) |movers|stocks moving|why .* "
    r"(?:stock|shares)|what's going on|trading higher|trading lower|halted|session (?:gainers|"
    r"losers)|mid-day|premarket (?:gainers|losers)|52-week")
ANALYST = re.compile(
    r"maintains|price target|upgrades|downgrades|initiates coverage|reiterates|analyst|"
    r"expectations for|earnings preview|options activity|whale|short interest|p/e ratio|"
    r"insights into")
CATEGORIES: tuple[tuple[str, re.Pattern], ...] = (
    ("fda", re.compile(r"\bfda\b|clinical|phase [123i]|\btrial\b|topline|clearance|"
                       r"breakthrough designation|orphan drug")),
    ("deal", re.compile(r"merger|acquire|acquisition|to be acquired|buyout|definitive agreement|"
                        r"go-private|tender offer")),
    ("contract", re.compile(r"contract|award|partnership|collaborat|agreement|order from|"
                            r"purchase order|selected by")),
    ("offering", re.compile(r"offering|private placement|registered direct|warrant|\bpriced\b|"
                            r"atm program|dilut")),
    ("earnings", re.compile(r"earnings|reports q[1-4]|quarterly results|\beps\b|"
                            r"financial results|guidance")),
)
CAT_NAMES = tuple(c for c, _ in CATEGORIES) + ("other",)
NEWS_FEATURES = ("f_news_mins_since", "f_news_count_24h", "f_news_offering_24h",
                 *(f"f_news_last_{c}" for c in CAT_NAMES))
_DAY_NS = 24 * 3600 * 10**9
_CAP_MIN = 1440.0


def classify(headline: str) -> str | None:
    """Category of a headline, or None when it must not be used (recap / analyst note)."""
    h = headline.lower()
    if RECAP.search(h) or ANALYST.search(h):
        return None
    for name, pat in CATEGORIES:
        if pat.search(h):
            return name
    return "other"


@dataclass
class NewsIndex:
    """symbol -> time-sorted (created_at ns, category) of usable headlines."""
    names = NEWS_FEATURES
    _ts: dict[str, np.ndarray] = field(default_factory=dict)
    _cat: dict[str, list[str]] = field(default_factory=dict)
    latency_s: float = 5.0

    @classmethod
    def from_frame(cls, df: pd.DataFrame, latency_s: float = 5.0) -> "NewsIndex":
        df = df[df["symbols"].astype(str).str.count(",") == 0].copy()
        df["cat"] = [classify(str(h)) for h in df["headline"]]
        df = df[df["cat"].notna()]
        # explicit ns: pandas 3 defaults to microsecond datetimes, and the lookups use ns
        df["ns"] = pd.to_datetime(df["created_at"], utc=True).dt.as_unit("ns").astype("int64")
        idx = cls(latency_s=latency_s)
        for sym, g in df.sort_values("ns").groupby(df["symbols"].str.upper()):
            idx._ts[sym] = g["ns"].to_numpy()
            idx._cat[sym] = g["cat"].tolist()
        return idx

    @classmethod
    def from_glob(cls, pattern: str = "data/news/*.parquet", latency_s: float = 5.0) -> "NewsIndex":
        parts = [pd.read_parquet(p) for p in sorted(glob.glob(pattern))]
        if not parts:
            raise FileNotFoundError(f"no news files match {pattern}")
        return cls.from_frame(pd.concat(parts, ignore_index=True), latency_s)

    def features(self, symbol: str, bar_open_ns: int) -> dict[str, float]:
        """Features for a decision at the CLOSE of the bar opening at ``bar_open_ns``."""
        out = {f: 0.0 for f in NEWS_FEATURES}
        out["f_news_mins_since"] = _CAP_MIN
        ts = self._ts.get(symbol.upper())
        if ts is None:
            return out
        cutoff = bar_open_ns + 60 * 10**9 - int(self.latency_s * 10**9)
        k = bisect_right(ts, cutoff)               # headlines strictly known by the cutoff
        if k == 0:
            return out
        last = int(ts[k - 1])
        out["f_news_mins_since"] = min(_CAP_MIN, (cutoff - last) / 6e10)
        lo = bisect_right(ts, cutoff - _DAY_NS)
        cats = self._cat[symbol.upper()][lo:k]
        out["f_news_count_24h"] = float(len(cats))
        out["f_news_offering_24h"] = float("offering" in cats)
        if cats:
            out[f"f_news_last_{cats[-1]}"] = 1.0
        return out
