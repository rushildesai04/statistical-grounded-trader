"""Exact decimal price thresholds: version ``price-decimal-v1`` (2026-11-25).

STATUS: PREPARED, NOT IN USE. No registered calculation, live check, ledger or report calls this module. The
registered replay keeps its own arithmetic (``binary-v0``, below) until the owner approves the dated amendment
``docs/findings/amendment_stop_arithmetic_2026-11-25.md``, which says how each cohort is treated. The two versions
are never mixed inside one record and neither replaces the other silently.

The defect (found 2026-11-24, GLXG's stop was touched to the cent). Every stop in the system is written
``high >= entry * (1 + 0.20)`` in binary floating point. 0.20 and most prices have no exact binary form, so the
product is the nearest double, sometimes a hair ABOVE the true level: for an entry of $5.15 it is 6.180000000000001,
and a print at exactly $6.18 (a 20.00% rise) is then NOT at or above the level. The rule says "at or above"; whether
a touch counts depends on the entry price. Same class: a long's target ``entry + 2 x (entry - stop)`` and the
short-sale price test ``low <= 0.9 x prior close``.

The correction: decimal arithmetic, no tolerance.
- A PRICE is the decimal number its source reported. Market data reaches us as JSON numbers; Python holds them as
  doubles; the shortest decimal that round-trips a double (``repr``) is exactly the number that was sent, for any
  price with up to 15 significant digits. ``price()`` reads it back that way.
- PRECISION POLICY (explicit). A reported price has at most ``MAX_DP`` = 6 decimal places. Quotes move in $0.01 at
  $1.00 and above and in $0.0001 below it (Regulation NMS Rule 612); trades can print between those ticks
  (midpoint and price-improved executions), which is why 4 places are common above $1 and why the bound is not 2.
  A number that needs more than 6 places is not a reported price (it was computed): ``price()`` REFUSES it
  (``NotAPrice``). It is never rounded into one.
- A THRESHOLD is computed exactly from prices and exact decimal rates (``Decimal("0.20")``) and is NEVER rounded:
  20% above $1.9692 is $2.36304, a level no quote can sit on, and the first print at or above it is $2.3631, not
  $2.36 and not $2.37. Rounding the level to a cent would move it by up to half a cent in either direction with no
  basis in the rule.
- COMPARISON (explicit): ``at_or_above(price, level)`` is ``price >= level`` on exact decimals; ``at_or_below`` is
  ``<=``. No epsilon anywhere. Arithmetic runs in a context that RAISES if any step would round, so "exact" is
  enforced, not assumed.

Pure: no network, no database, no clock.
"""
from __future__ import annotations

import math
from decimal import ROUND_CEILING, Context, Decimal, Inexact, InvalidOperation, Rounded

VERSION = "price-decimal-v1"
REGISTERED = "binary-v0"                # the arithmetic of the registered replay: entry * (1 + 0.20) in doubles
MAX_DP = 6                              # the most decimal places a reported price can carry (policy, see above)
STOP = Decimal("0.20")                  # the fade's stop, as an exact rate
RR = Decimal("2")                       # a long's target, in risks
SSR_DROP = Decimal("0.10")              # the short-sale price test: a decline of 10% or more from the prior close
PENNY, SUB_PENNY, DOLLAR = Decimal("0.01"), Decimal("0.0001"), Decimal("1")
_EXACT = Context(prec=60, traps=[Inexact, Rounded, InvalidOperation])   # any rounding is an error, never a result


class NotAPrice(ValueError):
    """The value is not a positive, finite number with at most MAX_DP decimal places."""


def price(x) -> Decimal:
    """The exact decimal a source reported. ``x``: a float (read back by its shortest round-trip form), an int, a
    Decimal or a decimal string. Refused: bool, NaN, infinities, zero, negatives, more than MAX_DP places."""
    if isinstance(x, bool) or x is None:
        raise NotAPrice(f"not a price: {x!r}")
    try:
        if isinstance(x, Decimal):
            d = x
        elif isinstance(x, int):
            d = Decimal(x)
        elif isinstance(x, str):
            d = Decimal(x.strip())
        else:
            f = float(x)                                  # numpy and pandas scalars included
            if not math.isfinite(f):
                raise NotAPrice(f"not a price: {x!r}")
            d = Decimal(repr(f))
    except (InvalidOperation, TypeError, ValueError) as exc:
        if isinstance(exc, NotAPrice):
            raise
        raise NotAPrice(f"not a price: {x!r}") from exc
    if not d.is_finite() or d <= 0:
        raise NotAPrice(f"not a price: {x!r}")
    d = d.normalize()
    if -d.as_tuple().exponent > MAX_DP:
        raise NotAPrice(f"{x!r} has more than {MAX_DP} decimal places: a computed number, not a reported price")
    return d


def rate(x) -> Decimal:
    """An exact rate (0.20, 2, 0.10): a Decimal, an int or a decimal string. A float is refused: 0.2 as a double is
    not 0.2, which is the defect this module removes."""
    if isinstance(x, bool) or isinstance(x, float) or x is None:
        raise TypeError(f"a rate must be exact (Decimal, int or str), not {x!r}")
    try:
        d = Decimal(x.strip()) if isinstance(x, str) else Decimal(x)
    except InvalidOperation as exc:
        raise TypeError(f"not a rate: {x!r}") from exc
    if not d.is_finite():
        raise TypeError(f"not a rate: {x!r}")
    return d


def level_above(entry, by=STOP) -> Decimal:
    """``entry x (1 + by)``, exact and unrounded: a short's stop level."""
    return _EXACT.multiply(price(entry), _EXACT.add(DOLLAR, rate(by)))


def level_below(ref, by=SSR_DROP) -> Decimal:
    """``ref x (1 - by)``, exact and unrounded: the price-test trigger under a prior close."""
    return _EXACT.multiply(price(ref), _EXACT.subtract(DOLLAR, rate(by)))


def long_target(entry, stop, rr=RR) -> Decimal:
    """``entry + rr x (entry - stop)``, exact and unrounded: a long's target."""
    e = price(entry)
    return _EXACT.add(e, _EXACT.multiply(rate(rr), _EXACT.subtract(e, price(stop))))


def short_target(entry, stop, rr=RR) -> Decimal:
    """``entry - rr x (stop - entry)``, exact and unrounded: a short's target (the stop is above the entry)."""
    e = price(entry)
    return _EXACT.subtract(e, _EXACT.multiply(rate(rr), _EXACT.subtract(price(stop), e)))


def at_or_above(px, level: Decimal) -> bool:
    """Has ``px`` (a reported price) reached ``level`` from below? Exact ``>=``; a touch counts."""
    return price(px) >= level


def at_or_below(px, level: Decimal) -> bool:
    """Has ``px`` (a reported price) reached ``level`` from above? Exact ``<=``; a touch counts."""
    return price(px) <= level


def quote_tick(px) -> Decimal:
    """The quoting increment at ``px``: $0.01 at $1.00 and above, $0.0001 below (Rule 612). Trades may print inside
    it; this is the grid a displayed bid or offer sits on."""
    return PENNY if price(px) >= DOLLAR else SUB_PENNY


def first_quote_at_or_above(level: Decimal) -> Decimal:
    """The lowest QUOTABLE price that is at or above ``level`` (for reports: where a resting offer would first meet a
    stop). The level itself is not changed by this."""
    lv = rate(level)
    if lv <= 0:
        raise NotAPrice(f"not a level: {level!r}")
    tick = PENNY if lv >= DOLLAR else SUB_PENNY
    return (lv / tick).to_integral_value(rounding=ROUND_CEILING) * tick


# --- the two versions side by side (for the amendment's measurements; neither is "the" result here) -----------------

def binary_short_stop(entry: float, high: float, by: float = 0.20) -> bool:
    """The registered comparison, as written in the replay (``binary-v0``)."""
    return high >= entry * (1 + by)


def decimal_short_stop(entry, high, by=STOP) -> bool:
    """The corrected comparison (``price-decimal-v1``)."""
    return at_or_above(high, level_above(entry, by))


def binary_long_target(entry: float, stop: float, high: float, rr: float = 2.0) -> bool:
    return high >= entry + rr * (entry - stop)


def decimal_long_target(entry, stop, high, rr=RR) -> bool:
    return at_or_above(high, long_target(entry, stop, rr))


def binary_price_test(prior_close: float, low: float) -> bool:
    return low <= 0.9 * prior_close


def decimal_price_test(prior_close, low, by=SSR_DROP) -> bool:
    return at_or_below(low, level_below(prior_close, by))


def short_cover(highs, opens, closes, entry, by=STOP) -> tuple[Decimal, int | None] | None:
    """The registered short replay's cover under ``price-decimal-v1``: the first bar whose high is at or above the
    exact level covers at max(level, that bar's open); otherwise the last close. Returns (cover, index of the stop
    bar or None for a timer exit); None with no bars. Same walk as ``alert_fade_test.cover``, exact comparisons."""
    highs, opens, closes = list(highs), list(opens), list(closes)
    if not highs or not (len(highs) == len(opens) == len(closes)):
        return None
    level = level_above(entry, by)
    for i, h in enumerate(highs):
        if at_or_above(h, level):
            return max(level, price(opens[i])), i
    return price(closes[-1]), None
