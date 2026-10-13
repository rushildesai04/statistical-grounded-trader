"""Strict grammar for the "NEWS , VOLUME , BREAKOUTS" Telegram alert channel.

All channel text is hostile input: it is size-bounded, parsed with explicit
per-alert regexes, and anything that does not match a known template becomes an
``Unparsed`` record rather than a guess (docs/IMPLEMENTATION_PLAN.md §12).

``parse_message`` returns ``list[ParsedItem]`` (a tagged union of ``ParsedAlert``,
``ParsedNews`` and ``Unparsed``). A message with several alert lines yields one
item per line; when nothing matches it returns ``[Unparsed(...)]`` (never empty),
so the collector always has a record to persist and a ``parse_status`` to set.

Observed templates (prices may carry 4 decimals, whitespace varies, emojis trail):
    "YMAT intraday high 3.57 💰"      -> intraday_high  price=3.57
    "YMAT halted 3.57 💰"            -> halted         price=3.57
    "YMAT open!"                     -> resumed        price=None
    "YMAT new high 4.49 💰"          -> new_high       price=4.49
    "WBUY  volume increasing  0.9549"-> volume_increasing price=0.9549
    "NCPL  gaining momentum  1.53"   -> gaining_momentum  price=1.53
    "SMTK after hours high 1.63 💰"  -> after_hours_high  price=1.63
  multi-line news block:
    "YMAT"
    "-No news"                       -> news has_news=False headline=""
    "FCHL"
    "-On October 09, J-Star ..."     -> news has_news=True  headline="On October 09, ..."
    "<optional company description>"
A forwarded/quoted "NEWS , VOLUME , BREAKOUTS" header line is ignored but the
following alert lines are kept.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Union

# --------------------------------------------------------------------------- bounds

MAX_TEXT_LEN = 4096          # Telegram's own message cap; reject anything larger.
MAX_LINES = 512             # hard bound on lines processed per message.
NEWS_HEADLINE_MAX = 500     # bound stored headline text.

# --------------------------------------------------------------------------- grammar

# 1-5 uppercase letters with an optional .A/.B share-class suffix.
_TICKER = r"[A-Z]{1,5}(?:\.[A-Z])?"
# Positive price, optionally with up to many decimals (channel uses up to 4).
_PRICE = r"\d+(?:\.\d+)?"
# Trailing junk: whitespace and emojis/symbols only (never word characters).
_TRAILER = r"\W*"

ALERT_TYPES: tuple[str, ...] = (
    "volume_increasing",
    "gaining_volume",
    "gaining_momentum",
    "new_high",
    "intraday_high",
    "after_hours_high",
    "halted",
    "resumed",
    "news",
)

# (phrase-regex, alert_type). Order is irrelevant: phrases are mutually exclusive.
_ALERT_PHRASES: tuple[tuple[str, str], ...] = (
    (r"volume\s+increasing", "volume_increasing"),
    (r"gaining\s+volume", "gaining_volume"),
    (r"gaining\s+momentum", "gaining_momentum"),
    (r"new\s+high", "new_high"),
    (r"intraday\s+high", "intraday_high"),
    (r"after\s+hours\s+high", "after_hours_high"),
    (r"halted", "halted"),
    (r"open!", "resumed"),
    (r"resumed", "resumed"),
)

# Each alert line: TICKER <phrase> [price] <trailing emoji>. fullmatch only.
_ALERT_RES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (
        re.compile(rf"({_TICKER})\s+{phrase}(?:\s+({_PRICE}))?\s*{_TRAILER}"),
        atype,
    )
    for phrase, atype in _ALERT_PHRASES
)

_BARE_TICKER_RE = re.compile(rf"({_TICKER})\s*{_TRAILER}")

Validator = Callable[[str], bool]


def _default_validator(symbol: str) -> bool:
    """Accept any well-formed uppercase ticker. Production wiring passes a
    universe-membership check instead (see ``TelegramCollector``)."""
    return re.fullmatch(_TICKER, symbol) is not None


# --------------------------------------------------------------------------- items


@dataclass(frozen=True, slots=True)
class ParsedAlert:
    channel: str
    message_id: int
    idx: int
    symbol: str
    alert_type: str
    price: float | None
    posted_at: datetime
    kind: str = "alert"


@dataclass(frozen=True, slots=True)
class ParsedNews:
    channel: str
    message_id: int
    idx: int
    symbol: str
    has_news: bool
    headline: str
    posted_at: datetime
    kind: str = "news"


@dataclass(frozen=True, slots=True)
class Unparsed:
    channel: str
    message_id: int
    posted_at: datetime
    reason: str
    text: str
    kind: str = "unparsed"


ParsedItem = Union[ParsedAlert, ParsedNews, Unparsed]


# --------------------------------------------------------------------------- helpers


def _require_utc(ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"posted_at must be tz-aware UTC, got {ts!r}")


def _is_channel_header(line: str) -> bool:
    norm = re.sub(r"\s+", "", line).upper().rstrip(".")
    return norm == "NEWS,VOLUME,BREAKOUTS"


def _match_alert(line: str, validator: Validator) -> tuple[str, str, float | None] | None:
    for pat, atype in _ALERT_RES:
        m = pat.fullmatch(line)
        if not m:
            continue
        symbol = m.group(1)
        if not validator(symbol):
            return None
        raw_price = m.group(2)
        price: float | None = None
        if raw_price is not None:
            price = float(raw_price)
            if price <= 0:
                return None
        return symbol, atype, price
    return None


def _bare_ticker(line: str, validator: Validator) -> str | None:
    m = _BARE_TICKER_RE.fullmatch(line)
    if not m:
        return None
    symbol = m.group(1)
    return symbol if validator(symbol) else None


def _interpret_news(body_lines: list[str]) -> tuple[bool, str]:
    dash = [ln.lstrip("-").strip() for ln in body_lines if ln.startswith("-")]
    other = [ln for ln in body_lines if not ln.startswith("-")]
    first = dash[0].lower().rstrip(".") if dash else ""
    if first in ("no news", "none", "no news yet"):
        return False, ""
    headline = " ".join([*dash, *other]).strip()
    return True, headline[:NEWS_HEADLINE_MAX]


# --------------------------------------------------------------------------- API


def parse_message(
    text: str,
    posted_at: datetime,
    channel: str,
    message_id: int,
    *,
    validator: Validator | None = None,
) -> list[ParsedItem]:
    """Parse one Telegram message into a list of tagged-union items.

    Never raises on bad content; returns ``[Unparsed(...)]`` when nothing matched
    or the input is out of bounds. ``posted_at`` must be tz-aware UTC.
    """
    _require_utc(posted_at)
    check = validator or _default_validator

    if not isinstance(text, str):
        return [Unparsed(channel, message_id, posted_at, "non-string", "")]
    if len(text) > MAX_TEXT_LEN:
        return [Unparsed(channel, message_id, posted_at, "oversize", text[:MAX_TEXT_LEN])]

    lines: list[str] = []
    for raw in text.splitlines():
        if len(lines) >= MAX_LINES:
            break
        s = raw.strip()
        if not s or _is_channel_header(s):
            continue
        lines.append(s)

    items: list[ParsedItem] = []
    idx = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]

        # News block: a bare ticker line immediately followed by a "-" line.
        sym = _bare_ticker(line, check)
        if sym is not None and i + 1 < n and lines[i + 1].startswith("-"):
            body: list[str] = []
            j = i + 1
            while j < n:
                nxt = lines[j]
                if _match_alert(nxt, check):
                    break
                if (
                    _bare_ticker(nxt, check) is not None
                    and j + 1 < n
                    and lines[j + 1].startswith("-")
                ):
                    break
                body.append(nxt)
                j += 1
            has_news, headline = _interpret_news(body)
            items.append(
                ParsedNews(channel, message_id, idx, sym, has_news, headline, posted_at)
            )
            idx += 1
            i = j
            continue

        alert = _match_alert(line, check)
        if alert is not None:
            a_sym, a_type, a_price = alert
            items.append(
                ParsedAlert(channel, message_id, idx, a_sym, a_type, a_price, posted_at)
            )
            idx += 1
        i += 1

    if not items:
        reason = "empty" if not lines else "no_known_template"
        return [Unparsed(channel, message_id, posted_at, reason, text[:MAX_TEXT_LEN])]
    return items
