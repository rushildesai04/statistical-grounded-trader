"""Universe filter: the point-in-time hard gate deciding which tickers are eligible.

A :class:`UniverseFilter` is a validated pydantic model (docs/IMPLEMENTATION_PLAN.md
§7.2 ``universe``, §12). Each criterion is OPTIONAL: ``None`` disables it so a filter
can be specified partially. Every enabled criterion is a HARD filter — a symbol that
cannot satisfy it (including when the feature needed to evaluate it is unknown) is
excluded. There is no soft path: Telegram-alerted symbols are evaluated like any other
candidate and must still clear float/price/dilution/volume gates (§12).

The filter is pure: it decides on a pre-computed feature mapping (built point-in-time
by :mod:`sgt.scanner.rank`) and never performs I/O. ``evaluate`` returns the list of
failed-criterion labels; an empty list means the symbol passed.
"""
from __future__ import annotations

from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Feature keys the filter reads (produced by sgt.scanner.rank._features). A value of
# None means "unknown"; an enabled criterion over an unknown feature fails closed.
F_PRICE = "price"
F_FREE_FLOAT = "free_float"
F_CUM_REL_VOL = "cum_rel_vol"
F_GAP_PCT = "gap_pct"
F_DOLLAR_VOLUME = "dollar_volume"
F_DILUTION = "dilution_flag"


class UniverseFilter(BaseModel):
    """Hard eligibility gate evaluated point-in-time at time ``t``.

    All thresholds are optional (``None`` disables the criterion). Boundaries are
    inclusive: a symbol exactly at a threshold passes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    float_max: float | None = Field(default=None, gt=0.0)
    price_min: float | None = Field(default=None, gt=0.0)
    price_max: float | None = Field(default=None, gt=0.0)
    min_rel_vol: float | None = Field(default=None, ge=0.0)
    min_gap_pct: float | None = Field(default=None)
    min_dollar_volume: float | None = Field(default=None, ge=0.0)
    exclude_dilution: bool = False

    @model_validator(mode="after")
    def _check_price_band(self) -> "UniverseFilter":
        if (self.price_min is not None and self.price_max is not None
                and self.price_min > self.price_max):
            raise ValueError("price_min must be <= price_max")
        return self

    def evaluate(self, features: Mapping[str, float | bool | None]) -> list[str]:
        """Return failed-criterion labels (empty == eligible).

        Fail-closed: an enabled criterion whose feature is None (unknown) fails, so a
        name whose float/gap/rel-vol we cannot establish is never admitted.
        """
        fails: list[str] = []

        price = features.get(F_PRICE)
        if self.price_min is not None:
            if not isinstance(price, (int, float)) or price < self.price_min:
                fails.append("price_min")
        if self.price_max is not None:
            if not isinstance(price, (int, float)) or price > self.price_max:
                fails.append("price_max")

        if self.float_max is not None:
            ff = features.get(F_FREE_FLOAT)
            if not isinstance(ff, (int, float)) or ff > self.float_max:
                fails.append("float_max")

        if self.min_rel_vol is not None:
            rv = features.get(F_CUM_REL_VOL)
            if not isinstance(rv, (int, float)) or rv < self.min_rel_vol:
                fails.append("min_rel_vol")

        if self.min_gap_pct is not None:
            gap = features.get(F_GAP_PCT)
            if not isinstance(gap, (int, float)) or gap < self.min_gap_pct:
                fails.append("min_gap_pct")

        if self.min_dollar_volume is not None:
            dv = features.get(F_DOLLAR_VOLUME)
            if not isinstance(dv, (int, float)) or dv < self.min_dollar_volume:
                fails.append("min_dollar_volume")

        if self.exclude_dilution and bool(features.get(F_DILUTION)):
            fails.append("exclude_dilution")

        return fails
