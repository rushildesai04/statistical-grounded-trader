"""Conservative execution cost model for the simulation engine.

The model is deliberately pessimistic (see docs/IMPLEMENTATION_PLAN.md §8): when we
cannot *measure* the spread from quote data we *estimate* it from the price bucket
and float, biased wide, so a backtest never flatters itself with fills it would not
get on a thin low-float name. Every output is an adverse (cost-increasing) number.

Half-spread defaults by price bucket (fraction of price, adverse per side):
    <$1    -> 1.00%   (penny names: wide, jumpy books)
    $1-5   -> 0.50%
    $5-20  -> 0.20%
    >$20   -> 0.05%
These are multiplied up for low-float names (free float < 5M shares -> x1.5) and for
size relative to the bar's volume (participation drag), and widened further when the
bar's volume is so thin that even a small order is a large share of it.
"""
from __future__ import annotations

from dataclasses import dataclass

from sgt.core.types import Side


@dataclass(frozen=True, slots=True)
class CostModel:
    """Immutable cost configuration. All percentages are *fractions* (0.01 == 1%)."""

    # Half-spread fraction of price, per price bucket, adverse per side.
    hs_under_1: float = 0.0100
    hs_1_to_5: float = 0.0050
    hs_5_to_20: float = 0.0020
    hs_over_20: float = 0.0005

    # Low-float penalty: free float below this many shares multiplies the half-spread.
    low_float_shares: float = 5_000_000.0
    low_float_mult: float = 1.5

    # Size/participation drag: slippage grows linearly with qty / bar_volume.
    # mult *= 1 + participation_drag_k * (qty / bar_volume).
    participation_drag_k: float = 2.0
    # When bar_volume is zero/unknown we cannot fill (handled by the broker) but if a
    # slippage estimate is still requested we apply this hard penalty multiplier.
    empty_volume_mult: float = 5.0

    # Max fraction of a bar's volume that can fill in one bar (participation cap).
    participation_cap: float = 0.01

    # Fee schedule (USD). Default zero (commission-free retail brokers).
    per_share_fee: float = 0.0
    per_order_fee: float = 0.0

    def __post_init__(self) -> None:
        for name in ("hs_under_1", "hs_1_to_5", "hs_5_to_20", "hs_over_20"):
            if getattr(self, name) < 0:
                raise ValueError(f"CostModel.{name} must be >= 0")
        if not 0 < self.participation_cap <= 1:
            raise ValueError("participation_cap must be in (0, 1]")
        if self.low_float_mult < 1 or self.low_float_shares <= 0:
            raise ValueError("low_float_mult must be >= 1 and low_float_shares > 0")
        if self.per_share_fee < 0 or self.per_order_fee < 0:
            raise ValueError("fees must be >= 0")

    def half_spread_pct(self, price: float) -> float:
        """Estimated half-spread as a fraction of price, by price bucket."""
        if price < 1.0:
            return self.hs_under_1
        if price < 5.0:
            return self.hs_1_to_5
        if price < 20.0:
            return self.hs_5_to_20
        return self.hs_over_20

    def max_fill_qty(self, bar_volume: float) -> int:
        """Whole shares fillable in one bar under the participation cap."""
        if bar_volume <= 0:
            return 0
        return int(bar_volume * self.participation_cap)

    def slippage(
        self,
        price: float,
        side: Side,
        qty: int,
        bar_volume: float,
        free_float: float | None = None,
        measured_spread_pct: float | None = None,
    ) -> float:
        """Absolute, non-negative adverse price adjustment for one fill.

        The caller applies the sign: add for BUY, subtract for SELL (a BUY pays up,
        a SELL receives less). ``side`` is accepted for a symmetric interface and
        future asymmetric calibration; the magnitude here is side-independent.

        ``measured_spread_pct`` (half-spread fraction from real quotes) overrides the
        bucket estimate when available. ``free_float`` in shares triggers the
        low-float penalty when below ``low_float_shares``.
        """
        if price <= 0:
            raise ValueError("slippage price must be > 0")
        if qty <= 0:
            raise ValueError("slippage qty must be > 0")
        hs_pct = measured_spread_pct if measured_spread_pct is not None else self.half_spread_pct(price)
        if hs_pct < 0:
            raise ValueError("measured_spread_pct must be >= 0")
        mult = 1.0
        if free_float is not None and free_float < self.low_float_shares:
            mult *= self.low_float_mult
        if bar_volume <= 0:
            mult *= self.empty_volume_mult
        else:
            participation = qty / bar_volume
            mult *= 1.0 + self.participation_drag_k * participation
        _ = side  # symmetric; retained for interface stability
        return price * hs_pct * mult

    def fees(self, qty: int) -> float:
        """Total fee for an execution of ``qty`` shares (per-order + per-share)."""
        if qty <= 0:
            raise ValueError("fee qty must be > 0")
        return self.per_order_fee + self.per_share_fee * qty


DEFAULT_COST_MODEL = CostModel()
