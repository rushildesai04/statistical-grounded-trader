"""2024-26 channel wording (amendment A1 of the Telegram pre-registration)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sgt.crowd.telegram_parser import ParsedAlert, ParsedNews, Unparsed, parse_message

TS = datetime(2025, 3, 3, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,atype,price", [
    ("ABCD volume increasing .9549", "volume_increasing", 0.9549),
    ("ABCD  volume increase 1.2", "volume_increasing", 1.2),
    ("ABCD volume increasing $2.31", "volume_increasing", 2.31),
    ("ABCD  volume spike  3.4", "volume_spike", 3.4),
    ("ABCD gaining volumed .55", "gaining_volume", 0.55),
    ("ABCD day high 4 💰", "intraday_high", 4.0),
    ("ABCD new day high 4.1 💰", "new_high", 4.1),
    ("ABCD  new high of day 4.2 🤑", "new_high", 4.2),
    ("ABCD  testing high of day", "testing_high", None),
    ("ABCD premarket high 2 💰", "premarket_high", 2.0),
    ("ABCD new premarket high .8 💰", "premarket_high", 0.8),
    ("ABCD over $3 💰🤑", "price_level", 3.0),
    ("ABCD at 2.50", "price_level", 2.5),
    ("ABCD open", "resumed", None),
    ("ABCD new high, halted 5.5 💰", "halted", 5.5),
    ("ABCD  volume spike , halted 6", "halted", 6.0),
])
def test_alert_templates(text, atype, price):
    [item] = parse_message(text, TS, "c", 1)
    assert isinstance(item, ParsedAlert)
    assert (item.symbol, item.alert_type, item.price) == ("ABCD", atype, price)


def test_news_block_without_dash():
    items = parse_message("PLBY\n\nPlayboy Signs $122 Million Deal\n\nMore text", TS, "c", 1)
    assert len(items) == 1 and isinstance(items[0], ParsedNews)
    assert items[0].symbol == "PLBY" and items[0].has_news
    assert items[0].headline.startswith("Playboy Signs")


def test_ticker_list_and_banners_stay_unparsed():
    assert isinstance(parse_message("🚨 Market closed (End of day)🚨", TS, "c", 1)[0], Unparsed)
    assert isinstance(parse_message("🚨 Spiker stocks on Halt🚨\n\n$ELPW 💰\n\n$YI 💰", TS, "c", 1)[0], Unparsed)


def test_inline_news():
    [item] = parse_message("YOOV - Concorde Secures Multi-Year Contracts Worth $10 Million", TS, "c", 1)
    assert isinstance(item, ParsedNews) and item.symbol == "YOOV" and item.has_news


def test_dash_price_is_not_news():
    assert isinstance(parse_message("ABCD - 2.5", TS, "c", 1)[0], Unparsed)
