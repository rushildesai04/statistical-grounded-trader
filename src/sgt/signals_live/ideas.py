"""Strict parser for the "OPTIONS & STOCKS TRADE IDEAS" channel (hostile input).

Stock call (2024-26 format):
    🚨 STOCKS / $PCLA [- HIGH RISK] / Ideal Entry: 8.36 / PT: 5% , 10% , 20%+ / SL: 7.02
Option call (logged only, phase 1):
    🚨 QQQ PUTS - 0DTE / Strike : 712 @ 2.65 / Trim @ 20% - 100%+ / SL : 50%
Anything else (trim updates, recaps, banners) returns None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MAX_TEXT = 4096
_TICKER = r"[A-Z]{1,5}"
_NUM = r"\d+(?:\.\d+)?|\.\d+"


@dataclass(frozen=True, slots=True)
class StockIdea:
    symbol: str
    entry: float
    targets_pct: tuple[float, ...]   # e.g. (0.05, 0.10, 0.20)
    stop: float
    high_risk: bool


@dataclass(frozen=True, slots=True)
class OptionIdea:
    underlying: str
    right: str          # "C" or "P"
    dte: int
    strike: float
    premium: float
    stop_pct: float | None


_ENTRY = re.compile(rf"Ideal\s+Entry\s*:\s*\$?({_NUM})", re.I)
_PT = re.compile(r"PT\s*:\s*([^\n]+)", re.I)
_SL = re.compile(rf"SL\s*:\s*\$?({_NUM})(\s*%)?", re.I)
_STOCK_SYM = re.compile(rf"\$({_TICKER})\b")
_OPT_HEAD = re.compile(rf"({_TICKER})\s+(CALLS?|PUTS?)\s*-\s*(\d+)\s*DTE", re.I)
_STRIKE = re.compile(rf"Strike\s*:\s*({_NUM})\s*@\s*\$?({_NUM})", re.I)


def parse_idea(text: str) -> StockIdea | OptionIdea | None:
    if not isinstance(text, str) or not text or len(text) > MAX_TEXT:
        return None
    if (m := _ENTRY.search(text)) is not None:
        return _stock(text, float(m.group(1)))
    if (h := _OPT_HEAD.search(text)) is not None and (s := _STRIKE.search(text)) is not None:
        sl = _SL.search(text)
        stop_pct = float(sl.group(1)) / 100 if sl and sl.group(2) else None
        return OptionIdea(h.group(1).upper(), "C" if h.group(2).upper().startswith("C") else "P",
                          int(h.group(3)), float(s.group(1)), float(s.group(2)), stop_pct)
    return None


def _stock(text: str, entry: float) -> StockIdea | None:
    sym = _STOCK_SYM.search(text)
    pt = _PT.search(text)
    sl = _SL.search(text)
    if sym is None or pt is None or sl is None or sl.group(2):
        return None
    targets = tuple(float(x) / 100 for x in re.findall(r"(\d+(?:\.\d+)?)\s*%", pt.group(1)))[:3]
    stop = float(sl.group(1))
    if not targets or any(t <= 0 or t > 1 for t in targets) or not (0 < stop < entry) or entry <= 0:
        return None
    return StockIdea(sym.group(1), entry, targets, stop, "HIGH RISK" in text.upper())
