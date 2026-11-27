"""What one exchange was offering when a long was booked (owner 2026-11-25).

"Preserve the registered candle-close entry reference, but add a separately labeled live executable-price estimate
using current bid/ask quotes, quote age, displayed liquidity and slippage. Do not substitute last trade for a
guaranteed execution price."

What the live book CAN read in time is the free feed's standing quote: the best bid and offer of ONE exchange (IEX).
The national best bid and offer is on the official feed, which refuses data younger than about 15 minutes. So the
live figure is a BOUND, and is labelled as one:
- the exchange's offer is at or above the national best offer (the national best is the lowest offer of all
  exchanges, this one included), so a market buy of the shares shown there costs AT MOST that price;
- it is not an estimate of the fill. In thin stocks one exchange's quote can be far wider than the national quote
  (2026-11-25 14:09 ET: FCUV 4.40 x 5.67 on IEX with the last trade at 5.17).
The official quote standing at the booking instant is read later, by the nightly execution ledger
(``entry_quote`` on a long's row, ``scripts/exec_ledger.py``), and reported beside the reference.

Nothing here decides anything: the booking, its registered reference price, the stop and the target are untouched.
The quote is external data: read strictly, bounded, and anything unexpected becomes a note, never a number.
Pure apart from the ``read`` callable it is handed.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Callable

import pandas as pd

from sgt.fast.fade_rule import ET

VENUE = "IEX (one exchange)"
NOTE_MAX = 120
Reader = Callable[[str], dict]      # symbol -> {"bid", "ask", "bid_size", "ask_size", "at"} or {"why": "..."}


def _price(x) -> float | None:
    ok = isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0
    return float(x) if ok else None


def _size(x) -> float | None:
    ok = isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x >= 0
    return float(x) if ok else None


def venue(read: Reader | None, symbol: str, booked: float, now: datetime, shares: float | None = None) -> dict:
    """The exchange's quote at the booking, as fields for the long's ``entry_ref``; ``venue_note`` alone when there is
    no usable offer. Never raises."""
    if read is None:
        return {}
    try:
        q = read(symbol)
    except Exception as exc:  # noqa: BLE001 - evidence only; the long is already booked
        return {"venue_note": f"quote read failed ({type(exc).__name__})"}
    if not isinstance(q, dict):
        return {"venue_note": "no quote to read"}
    if q.get("why"):
        return {"venue_note": str(q["why"])[:NOTE_MAX]}
    ask, bid = _price(q.get("ask")), _price(q.get("bid"))
    if ask is None:
        return {"venue_note": "the exchange shows no offer"}
    if bid is not None and bid > ask:
        return {"venue_note": "crossed quote (bid above the offer): not used"}
    try:
        at = pd.Timestamp(str(q.get("at")))
        if at.tzinfo is None or pd.isna(at):
            return {"venue_note": "the quote carries no time"}
        age = (pd.Timestamp(now) - at).total_seconds()
    except (ValueError, TypeError):
        return {"venue_note": "the quote carries no time"}
    if not _price(booked):
        return {"venue_note": "no booked price to compare with"}
    ask_size = _size(q.get("ask_size"))
    out = {"venue": VENUE, "venue_ask": ask, "venue_ask_size": ask_size, "venue_bid": bid,
           "venue_bid_size": _size(q.get("bid_size")), "venue_at": at.isoformat(),
           "venue_age_s": round(max(0.0, age), 1), "venue_ask_vs_booked": round(ask / float(booked) - 1, 5)}
    if bid is not None:
        out["venue_width"] = round((ask - bid) / ((ask + bid) / 2), 5)
    if shares is not None and _price(shares) and ask_size is not None:
        out.update(order_shares=round(float(shares), 1), venue_covers_order=bool(ask_size >= float(shares)))
    return out


def venue_line(ref: dict) -> str:
    """The part of the LONG IN card's reference line about the exchange's offer ("" when there is none to show)."""
    if not ref.get("venue_ask"):
        return ""
    size = ref.get("venue_ask_size")
    shown = f" x {size:,.0f}" if size is not None else ""
    at = pd.Timestamp(ref["venue_at"]).tz_convert(ET)
    line = (f" IEX offer ${ref['venue_ask']:.2f}{shown} at {at:%H:%M:%S} ({ref['venue_age_s']:.0f} s old, "
            f"{ref['venue_ask_vs_booked']:+.1%} vs booked): one exchange's offer, the most a market buy of those shares "
            f"would cost, not a fill")
    if ref.get("venue_covers_order") is False:
        line += f"; it shows fewer shares than the order ({ref['order_shares']:,.0f})"
    return line + "."
