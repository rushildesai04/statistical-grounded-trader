"""Table-driven tests for the strict Telegram alert grammar."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sgt.crowd.telegram_parser import (
    MAX_TEXT_LEN,
    ParsedAlert,
    ParsedNews,
    Unparsed,
    parse_message,
)

T = datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc)
CHAN = "news_volume_breakouts"


def _parse(text: str):
    return parse_message(text, T, CHAN, 101)


# (text, symbol, alert_type, price)
ALERT_CASES = [
    ("YMAT intraday high 3.57 💰", "YMAT", "intraday_high", 3.57),
    ("YMAT halted 3.57 💰", "YMAT", "halted", 3.57),
    ("YMAT new high 4.49 💰", "YMAT", "new_high", 4.49),
    ("WFF halted 33.01 🤑", "WFF", "halted", 33.01),
    ("WFF  gaining volume  13.56", "WFF", "gaining_volume", 13.56),
    ("WFF new high 33.01 💰", "WFF", "new_high", 33.01),
    ("WBUY  volume increasing  0.9549", "WBUY", "volume_increasing", 0.9549),
    ("NCPL  gaining momentum  1.53", "NCPL", "gaining_momentum", 1.53),
    ("SMTK after hours high 1.63 💰", "SMTK", "after_hours_high", 1.63),
    ("FCHL  volume increasing  1.37", "FCHL", "volume_increasing", 1.37),
]


@pytest.mark.parametrize("text,symbol,atype,price", ALERT_CASES)
def test_alert_templates(text, symbol, atype, price):
    items = _parse(text)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ParsedAlert)
    assert item.symbol == symbol
    assert item.alert_type == atype
    assert item.price == pytest.approx(price)
    assert item.idx == 0


def test_open_is_resumed_no_price():
    items = _parse("YMAT open!")
    assert len(items) == 1
    assert isinstance(items[0], ParsedAlert)
    assert items[0].alert_type == "resumed"
    assert items[0].price is None


def test_double_spaces_and_trailing_emoji():
    items = _parse("WBUY   volume   increasing   0.9549  💰🤑")
    assert isinstance(items[0], ParsedAlert)
    assert items[0].symbol == "WBUY"
    assert items[0].price == pytest.approx(0.9549)


def test_forwarded_header_ignored_multiple_alerts():
    text = "NEWS , VOLUME , BREAKOUTS\nWFF  gaining volume  13.56\nWFF new high 33.01 💰"
    items = _parse(text)
    assert len(items) == 2
    assert all(isinstance(i, ParsedAlert) for i in items)
    assert [i.alert_type for i in items] == ["gaining_volume", "new_high"]
    assert [i.idx for i in items] == [0, 1]


def test_news_block_with_headline():
    text = "FCHL\n-On October 09, J-Star Holding Announces a big deal\nFCHL Corp makes widgets."
    items = _parse(text)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ParsedNews)
    assert item.symbol == "FCHL"
    assert item.has_news is True
    assert "J-Star Holding" in item.headline
    assert "widgets" in item.headline


def test_news_block_no_news():
    text = "YMAT\n-No news"
    items = _parse(text)
    assert len(items) == 1
    item = items[0]
    assert isinstance(item, ParsedNews)
    assert item.has_news is False
    assert item.headline == ""


def test_pinned_welcome_message_unparsed():
    text = "Welcome NEW MEMBERS, NOTE : not every alert means you should HURRY AN..."
    items = _parse(text)
    assert len(items) == 1
    assert isinstance(items[0], Unparsed)
    assert items[0].reason == "no_known_template"


def test_oversize_rejected():
    items = _parse("YMAT new high 1.00 " + "x" * (MAX_TEXT_LEN + 10))
    assert len(items) == 1
    assert isinstance(items[0], Unparsed)
    assert items[0].reason == "oversize"


def test_lowercase_ticker_rejected():
    items = _parse("wff new high 33.01")
    assert isinstance(items[0], Unparsed)


def test_bad_ticker_too_long_rejected():
    items = _parse("TOOLONG new high 1.00")
    assert isinstance(items[0], Unparsed)


def test_pluggable_validator_rejects_unknown_symbol():
    universe = {"YMAT"}
    items = parse_message("WFF new high 33.01", T, CHAN, 7, validator=lambda s: s in universe)
    assert isinstance(items[0], Unparsed)
    ok = parse_message("YMAT new high 4.49", T, CHAN, 8, validator=lambda s: s in universe)
    assert isinstance(ok[0], ParsedAlert)


def test_bare_ticker_without_dash_is_not_news():
    # A lone ticker with no following "-" line must not be guessed into anything.
    items = _parse("YMAT")
    assert isinstance(items[0], Unparsed)


def test_share_class_ticker():
    items = _parse("BRK.B new high 410.00")
    assert isinstance(items[0], ParsedAlert)
    assert items[0].symbol == "BRK.B"


def test_zero_price_rejected():
    items = _parse("YMAT new high 0")
    assert isinstance(items[0], Unparsed)


def test_requires_utc():
    with pytest.raises(ValueError):
        parse_message("YMAT open!", datetime(2026, 10, 9, 18, 30), CHAN, 1)


def test_mixed_alert_and_news_block():
    text = (
        "NEWS , VOLUME , BREAKOUTS\n"
        "YMAT new high 4.49 💰\n"
        "FCHL\n"
        "-No news\n"
        "WFF halted 33.01 🤑"
    )
    items = _parse(text)
    kinds = [type(i).__name__ for i in items]
    assert kinds == ["ParsedAlert", "ParsedNews", "ParsedAlert"]
    assert [i.idx for i in items] == [0, 1, 2]
