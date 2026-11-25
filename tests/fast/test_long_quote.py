"""One exchange's quote at a long's booking: a labelled bound beside the registered reference, never a fill."""
from __future__ import annotations

from datetime import datetime, timezone

from sgt.fast import long_quote as lq

NOW = datetime(2026, 11, 25, 17, 52, 6, 890000, tzinfo=timezone.utc)          # FCUV's booking, 12:52:06.89 ET
QUOTE = {"bid": 6.50, "ask": 6.59, "bid_size": 100, "ask_size": 100, "at": "2026-11-25T17:52:04.890000+00:00"}


def reader(q):
    return lambda symbol: q


def test_a_usable_quote_gives_the_offer_its_age_its_size_and_its_distance_from_the_booked_price():
    v = lq.venue(reader(QUOTE), "FCUV", 6.60, NOW, shares=33.0)
    assert v == {"venue": "IEX (one exchange)", "venue_ask": 6.59, "venue_ask_size": 100.0, "venue_bid": 6.5,
                 "venue_bid_size": 100.0, "venue_at": "2026-11-25T17:52:04.890000+00:00", "venue_age_s": 2.0,
                 "venue_ask_vs_booked": round(6.59 / 6.60 - 1, 5), "venue_width": round(0.09 / 6.545, 5),
                 "order_shares": 33.0, "venue_covers_order": True}
    assert lq.venue_line(v) == (" IEX offer $6.59 x 100 at 12:52:04 (2 s old, -0.2% vs booked): one exchange's offer, "
                                "the most a market buy of those shares would cost, not a fill.")


def test_an_offer_smaller_than_the_order_is_said():
    v = lq.venue(reader(QUOTE), "FCUV", 6.60, NOW, shares=221.0)
    assert v["venue_covers_order"] is False and v["order_shares"] == 221.0
    assert lq.venue_line(v).endswith("not a fill; it shows fewer shares than the order (221).")


def test_a_wide_one_exchange_quote_is_recorded_as_it_is_and_never_narrowed():
    # 2026-11-25 14:09 ET, FCUV on IEX: 4.40 x 5.67 while the last trade was 5.17. A bound, and a loose one.
    v = lq.venue(reader({**QUOTE, "bid": 4.40, "ask": 5.67}), "FCUV", 5.17, NOW)
    assert v["venue_ask"] == 5.67 and v["venue_ask_vs_booked"] == round(5.67 / 5.17 - 1, 5)
    assert v["venue_width"] == round(1.27 / 5.035, 5) and "order_shares" not in v


def test_anything_unexpected_is_a_note_and_never_a_number():
    cases = {"the exchange shows no offer": {**QUOTE, "ask": 0},
             "the exchange shows no offer ": {**QUOTE, "ask": None},
             "the exchange shows no offer  ": {**QUOTE, "ask": float("nan")},
             "the exchange shows no offer   ": {**QUOTE, "ask": True},
             "crossed quote (bid above the offer): not used": {**QUOTE, "bid": 6.70},
             "the quote carries no time": {**QUOTE, "at": "2026-11-25T17:52:04"},
             "the quote carries no time ": {**QUOTE, "at": "soon"},
             "the quote carries no time  ": {**QUOTE, "at": None},
             "the quote request failed (HTTP 500)": {"why": "the quote request failed (HTTP 500)"}}
    for note, q in cases.items():
        assert lq.venue(reader(q), "FCUV", 6.60, NOW) == {"venue_note": note.strip()}, note
    assert lq.venue(reader("junk"), "FCUV", 6.60, NOW) == {"venue_note": "no quote to read"}
    assert lq.venue(reader(QUOTE), "FCUV", 0.0, NOW) == {"venue_note": "no booked price to compare with"}
    assert lq.venue(reader({"why": "x" * 500}), "FCUV", 6.60, NOW)["venue_note"] == "x" * 120      # bounded

    def boom(symbol):
        raise RuntimeError("down")
    assert lq.venue(boom, "FCUV", 6.60, NOW) == {"venue_note": "quote read failed (RuntimeError)"}
    assert lq.venue(None, "FCUV", 6.60, NOW) == {} and lq.venue_line({"venue_note": "x"}) == ""


def test_a_one_sided_quote_and_a_quote_from_the_future_are_kept_without_inventing_anything():
    v = lq.venue(reader({**QUOTE, "bid": None, "bid_size": None, "at": "2026-11-25T17:52:09+00:00"}), "FCUV", 6.60, NOW)
    assert v["venue_bid"] is None and "venue_width" not in v and v["venue_age_s"] == 0.0      # clock skew: not negative
    v = lq.venue(reader({**QUOTE, "ask_size": None}), "FCUV", 6.60, NOW, shares=33.0)
    assert v["venue_ask_size"] is None and "venue_covers_order" not in v                       # unknown size: no claim
    assert " IEX offer $6.59 at 12:52:04 (" in lq.venue_line(v)
