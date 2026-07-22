"""Extract validated ticker mentions from free text.

Precision over recall: a missed mention is cheaper than a poisoned feature.
Rules:
  * `$TICK` cashtags are trusted when the symbol is in the universe.
  * Bare uppercase tokens are trusted only if in the universe AND not in the
    stopword blocklist of common WSB/finance non-tickers (DD, CEO, YOLO, ...).
"""
from __future__ import annotations

import re

# Common uppercase tokens on WSB/finance forums that collide with real tickers.
STOPWORDS: set[str] = {
    "DD", "IT", "CEO", "CFO", "CTO", "IPO", "ATH", "ER", "PR", "AI", "EV",
    "ETF", "GDP", "FED", "FOMC", "IMO", "TLDR", "EOD", "AH", "PM", "OTM",
    "ITM", "DTE", "PT", "EU", "UK", "US", "USA", "AKA", "IIRC", "AF", "LOL",
    "WTF", "ETC", "RIP", "HODL", "MOON", "YOLO", "FD", "FDS", "WSB", "EPS",
    "ALL", "ANY", "FOR", "ARE", "NEW", "NOW", "ONE", "OUT", "BIG", "LOW",
    "TOP", "END", "RED", "CAN", "GET", "GO", "ON", "AT", "BE", "DO", "OR",
    "SO", "TO", "UP", "IF", "IS", "OK", "NO", "MY", "WE", "AM", "AN", "AS",
    "BY", "HE", "ME", "OF", "THE", "AND", "BUT", "YOU", "NOT", "WAS", "CALL",
    "PUT", "PUTS", "CALLS", "BUY", "SELL", "HOLD", "GAIN", "LOSS", "BULL",
    "BEAR", "OG", "NFA", "TA", "FA", "IV", "OI",
}

_CASHTAG = re.compile(r"\$([A-Za-z]{1,6})\b")
_BARE = re.compile(r"\b([A-Z]{1,6})\b")


def extract_tickers(text: str, universe: set[str]) -> set[str]:
    """Return the set of validated tickers mentioned in `text`."""
    if not text:
        return set()
    found: set[str] = set()

    for m in _CASHTAG.finditer(text):
        sym = m.group(1).upper()
        if sym in universe:
            found.add(sym)

    for m in _BARE.finditer(text):
        sym = m.group(1)
        if sym in STOPWORDS:
            continue
        if sym in universe:
            found.add(sym)

    return found
