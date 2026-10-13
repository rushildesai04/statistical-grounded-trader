"""Signal / structure / context conditions and the discriminated condition union.

Every condition is a pydantic model with a ``kind`` ``Literal`` discriminator, a
grammar :class:`Family`, the :class:`Role` set it may fill, an optional
``CORRELATION_GROUP`` (signals in the same group count once in a k-of-m
confirmation), the :class:`IndicatorSpec`s it reads, and a pure ``evaluate`` that
returns ``False`` on any missing/``None`` input (warm-up never raises).

Family note (deviation from the brief's indicator tag): PSAR conditions are
grammar family ``STRUCTURE`` (a price-position check relative to the SAR level),
not ``TREND``. The brief simultaneously requires (a) ``trader_v0`` to validate with
an EMA-cross *trigger* and a ``psar_state`` *confirmation*, and (b) every
confirmation's family to differ from the trigger's family. With PSAR as TREND those
are contradictory; classifying PSAR as STRUCTURE resolves it and keeps psar_flip
trigger-capable (§7.1 lists Structure as a trigger family).
"""
from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Union

from pydantic import Field, TypeAdapter, field_serializer

from sgt.core.interfaces import IndicatorSpec
from sgt.strategy.base import Condition, EvalView, Family, Role
from sgt.strategy.exits import (
    SessionEnd,
    StopLossPct,
    TakeProfitPct,
    TimeStopMinutes,
    TrailingStopPct,
)

_MA_CROSS = "ma_cross"


def _f(x: float | None) -> bool:
    return x is not None


# --------------------------------------------------------------------------- trend


class EmaCrossUp(Condition):
    kind: Literal["ema_cross_up"] = "ema_cross_up"
    fast: int
    slow: int

    FAMILY: ClassVar[Family] = Family.TREND
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.TRIGGER, Role.EXIT})
    CORRELATION_GROUP: ClassVar[str | None] = _MA_CROSS

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("ema", n=self.fast), IndicatorSpec.of("ema", n=self.slow)]

    def evaluate(self, view: EvalView) -> bool:
        fk = self._out(IndicatorSpec.of("ema", n=self.fast), "value")
        sk = self._out(IndicatorSpec.of("ema", n=self.slow), "value")
        fn, sn, fp, sp = view.now(fk), view.now(sk), view.prev(fk), view.prev(sk)
        if not (_f(fn) and _f(sn) and _f(fp) and _f(sp)):
            return False
        return fn > sn and fp <= sp


class EmaCrossDown(Condition):
    kind: Literal["ema_cross_down"] = "ema_cross_down"
    fast: int
    slow: int

    FAMILY: ClassVar[Family] = Family.TREND
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.TRIGGER, Role.EXIT})
    CORRELATION_GROUP: ClassVar[str | None] = _MA_CROSS

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("ema", n=self.fast), IndicatorSpec.of("ema", n=self.slow)]

    def evaluate(self, view: EvalView) -> bool:
        fk = self._out(IndicatorSpec.of("ema", n=self.fast), "value")
        sk = self._out(IndicatorSpec.of("ema", n=self.slow), "value")
        fn, sn, fp, sp = view.now(fk), view.now(sk), view.prev(fk), view.prev(sk)
        if not (_f(fn) and _f(sn) and _f(fp) and _f(sp)):
            return False
        return fn < sn and fp >= sp


class MacdLineCrossUp(Condition):
    kind: Literal["macd_line_cross_up"] = "macd_line_cross_up"
    fast: int = 12
    slow: int = 26
    signal: int = 9

    FAMILY: ClassVar[Family] = Family.TREND
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.TRIGGER, Role.CONFIRM, Role.EXIT})
    CORRELATION_GROUP: ClassVar[str | None] = _MA_CROSS

    def _spec(self) -> IndicatorSpec:
        return IndicatorSpec.of("macd", fast=self.fast, slow=self.slow, signal=self.signal)

    def required_specs(self) -> list[IndicatorSpec]:
        return [self._spec()]

    def evaluate(self, view: EvalView) -> bool:
        lk, sk = self._out(self._spec(), "line"), self._out(self._spec(), "signal")
        ln, sn, lp, sp = view.now(lk), view.now(sk), view.prev(lk), view.prev(sk)
        if not (_f(ln) and _f(sn) and _f(lp) and _f(sp)):
            return False
        return ln > sn and lp <= sp


class SupertrendState(Condition):
    kind: Literal["supertrend_state"] = "supertrend_state"
    state: Literal["up", "down"]
    n: int = 10
    mult: float = 3.0

    FAMILY: ClassVar[Family] = Family.TREND
    ROLES: ClassVar[frozenset[Role]] = frozenset(
        {Role.TRIGGER, Role.CONFIRM, Role.FILTER, Role.EXIT})
    CORRELATION_GROUP: ClassVar[str | None] = _MA_CROSS

    def _spec(self) -> IndicatorSpec:
        return IndicatorSpec.of("supertrend", n=self.n, mult=self.mult)

    def required_specs(self) -> list[IndicatorSpec]:
        return [self._spec()]

    def evaluate(self, view: EvalView) -> bool:
        t = view.now(self._out(self._spec(), "trend"))
        if not _f(t):
            return False
        return t > 0 if self.state == "up" else t < 0


# --------------------------------------------------------------------------- momentum


class MacdHistState(Condition):
    kind: Literal["macd_hist_state"] = "macd_hist_state"
    state: Literal["Up1", "Up2", "Down1", "Down2"]
    fast: int = 12
    slow: int = 26
    signal: int = 9

    FAMILY: ClassVar[Family] = Family.MOMENTUM
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.SETUP, Role.FILTER, Role.CONFIRM, Role.EXIT})

    def _spec(self) -> IndicatorSpec:
        return IndicatorSpec.of("macd", fast=self.fast, slow=self.slow, signal=self.signal)

    def required_specs(self) -> list[IndicatorSpec]:
        return [self._spec()]

    def evaluate(self, view: EvalView) -> bool:
        hk = self._out(self._spec(), "hist")
        h, p = view.now(hk), view.prev(hk)
        if not (_f(h) and _f(p)):
            return False
        if self.state == "Up1":
            return h > 0 and h > p
        if self.state == "Up2":
            return h > 0 and h <= p
        if self.state == "Down1":
            return h < 0 and h < p
        return h < 0 and h >= p  # Down2


class RsiLt(Condition):
    kind: Literal["rsi_lt"] = "rsi_lt"
    n: int = 14
    value: float

    FAMILY: ClassVar[Family] = Family.MOMENTUM
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.FILTER, Role.SETUP, Role.CONFIRM, Role.EXIT})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("rsi", n=self.n)]

    def evaluate(self, view: EvalView) -> bool:
        v = view.now(self._out(IndicatorSpec.of("rsi", n=self.n), "value"))
        return _f(v) and v < self.value


class RsiGt(Condition):
    kind: Literal["rsi_gt"] = "rsi_gt"
    n: int = 14
    value: float

    FAMILY: ClassVar[Family] = Family.MOMENTUM
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.FILTER, Role.SETUP, Role.CONFIRM, Role.EXIT})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("rsi", n=self.n)]

    def evaluate(self, view: EvalView) -> bool:
        v = view.now(self._out(IndicatorSpec.of("rsi", n=self.n), "value"))
        return _f(v) and v > self.value


# --------------------------------------------------------------------------- flow


class RelVolGt(Condition):
    kind: Literal["rel_vol_gt"] = "rel_vol_gt"
    lookback: int = 20
    value: float
    # "value" = this bar vs same minute in prior sessions; "cum" = cumulative session
    # volume vs prior sessions at the same minute ("is the stock in play today").
    output: Literal["value", "cum"] = "value"

    FAMILY: ClassVar[Family] = Family.FLOW
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.CONFIRM, Role.FILTER, Role.REGIME})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("relvol", lookback_sessions=self.lookback)]

    def evaluate(self, view: EvalView) -> bool:
        v = view.now(self._out(IndicatorSpec.of("relvol", lookback_sessions=self.lookback), self.output))
        return _f(v) and v > self.value


class PvtState(Condition):
    kind: Literal["pvt_state"] = "pvt_state"
    state: Literal["above_signal", "below_signal"]
    signal_n: int = 14
    signal_kind: Literal["sma", "ema"] = "sma"

    FAMILY: ClassVar[Family] = Family.FLOW
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.CONFIRM, Role.FILTER, Role.EXIT})

    def _spec(self) -> IndicatorSpec:
        return IndicatorSpec.of("pvt", signal_n=self.signal_n, signal_kind=self.signal_kind)

    def required_specs(self) -> list[IndicatorSpec]:
        return [self._spec()]

    def evaluate(self, view: EvalView) -> bool:
        p = view.now(self._out(self._spec(), "pvt"))
        s = view.now(self._out(self._spec(), "signal"))
        if not (_f(p) and _f(s)):
            return False
        return p > s if self.state == "above_signal" else p < s


class PvtBullishDivergence(Condition):
    kind: Literal["pvt_bullish_divergence"] = "pvt_bullish_divergence"
    lookback: int = 15

    FAMILY: ClassVar[Family] = Family.FLOW
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.SETUP, Role.CONFIRM})

    def _spec(self) -> IndicatorSpec:
        return IndicatorSpec.of("pvt")

    def required_specs(self) -> list[IndicatorSpec]:
        return [self._spec()]

    def history_window(self) -> int:
        return self.lookback

    def evaluate(self, view: EvalView) -> bool:
        need = self.lookback - 1
        if self.lookback < 2 or len(view.history) < need:
            return False
        key = self._out(self._spec(), "pvt")
        prior_lows = list(view.history.lows[-need:])
        prior_pvts = [s.get(key) for s in view.history.snaps[-need:]]
        pvt_now = view.now(key)
        if not _f(pvt_now) or any(not _f(x) for x in prior_pvts):
            return False
        price_lower_low = view.bar.low < min(prior_lows)
        pvt_not_lower = pvt_now > min(prior_pvts)
        return price_lower_low and pvt_not_lower


class VolumeExhaustion(Condition):
    kind: Literal["volume_exhaustion"] = "volume_exhaustion"
    price_rising_bars: int = 3
    volume_falling_bars: int = 3

    FAMILY: ClassVar[Family] = Family.FLOW
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.EXIT, Role.SETUP})

    def history_window(self) -> int:
        return max(self.price_rising_bars, self.volume_falling_bars)

    def evaluate(self, view: EvalView) -> bool:
        r, fbar = self.price_rising_bars, self.volume_falling_bars
        if r < 1 or fbar < 1 or len(view.history) < max(r, fbar):
            return False
        closes = list(view.history.closes[-r:]) + [view.bar.close]
        vols = list(view.history.volumes[-fbar:]) + [view.bar.volume]
        rising = all(closes[i] > closes[i - 1] for i in range(1, len(closes)))
        falling = all(vols[i] < vols[i - 1] for i in range(1, len(vols)))
        return rising and falling


class VwapAbove(Condition):
    kind: Literal["vwap_above"] = "vwap_above"
    anchor: Literal["session", "rth"] = "session"

    FAMILY: ClassVar[Family] = Family.FLOW
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.CONFIRM, Role.FILTER, Role.REGIME})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("vwap", anchor=self.anchor)]

    def evaluate(self, view: EvalView) -> bool:
        v = view.now(self._out(IndicatorSpec.of("vwap", anchor=self.anchor), "value"))
        return _f(v) and view.bar.close > v


class VwapBelow(Condition):
    kind: Literal["vwap_below"] = "vwap_below"
    anchor: Literal["session", "rth"] = "session"

    FAMILY: ClassVar[Family] = Family.FLOW
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.CONFIRM, Role.FILTER, Role.REGIME})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("vwap", anchor=self.anchor)]

    def evaluate(self, view: EvalView) -> bool:
        v = view.now(self._out(IndicatorSpec.of("vwap", anchor=self.anchor), "value"))
        return _f(v) and view.bar.close < v


class FloatRotationGt(Condition):
    kind: Literal["float_rotation_gt"] = "float_rotation_gt"
    value: float

    FAMILY: ClassVar[Family] = Family.FLOW
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.CONFIRM, Role.FILTER, Role.REGIME})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("float_rotation")]

    def evaluate(self, view: EvalView) -> bool:
        v = view.now(self._out(IndicatorSpec.of("float_rotation"), "value"))
        return _f(v) and v > self.value


# --------------------------------------------------------------------------- volatility / structure


class AdxGt(Condition):
    kind: Literal["adx_gt"] = "adx_gt"
    n: int = 14
    value: float

    FAMILY: ClassVar[Family] = Family.VOLATILITY
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.REGIME, Role.FILTER})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("adx", n=self.n)]

    def evaluate(self, view: EvalView) -> bool:
        v = view.now(self._out(IndicatorSpec.of("adx", n=self.n), "adx"))
        return _f(v) and v > self.value


class PsarState(Condition):
    kind: Literal["psar_state"] = "psar_state"
    state: Literal["below_price", "above_price"]

    FAMILY: ClassVar[Family] = Family.STRUCTURE
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.CONFIRM, Role.FILTER, Role.SETUP, Role.EXIT})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("psar")]

    def evaluate(self, view: EvalView) -> bool:
        t = view.now(self._out(IndicatorSpec.of("psar"), "trend"))
        if not _f(t):
            return False
        # PSAR trend +1 == uptrend == SAR printed below price.
        return t > 0 if self.state == "below_price" else t < 0


class PsarFlip(Condition):
    kind: Literal["psar_flip"] = "psar_flip"
    direction: Literal["up", "down"]

    FAMILY: ClassVar[Family] = Family.STRUCTURE
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.TRIGGER, Role.CONFIRM, Role.EXIT})

    def required_specs(self) -> list[IndicatorSpec]:
        return [IndicatorSpec.of("psar")]

    def evaluate(self, view: EvalView) -> bool:
        key = self._out(IndicatorSpec.of("psar"), "trend")
        n, p = view.now(key), view.prev(key)
        if not (_f(n) and _f(p)):
            return False
        return (n > 0 and p < 0) if self.direction == "up" else (n < 0 and p > 0)


class HodBreak(Condition):
    kind: Literal["hod_break"] = "hod_break"

    FAMILY: ClassVar[Family] = Family.STRUCTURE
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.TRIGGER, Role.CONFIRM})

    def evaluate(self, view: EvalView) -> bool:
        hi = view.session_high_prior
        return hi is not None and view.bar.close > hi


class PremarketHighBreak(Condition):
    kind: Literal["premarket_high_break"] = "premarket_high_break"

    FAMILY: ClassVar[Family] = Family.STRUCTURE
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.TRIGGER, Role.CONFIRM})

    def evaluate(self, view: EvalView) -> bool:
        hi = view.premarket_high
        return hi is not None and view.bar.close > hi


# --------------------------------------------------------------------------- context


class SessionWindowIn(Condition):
    kind: Literal["session_window_in"] = "session_window_in"
    windows: frozenset[str]

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.REGIME})

    @field_serializer("windows")
    def _serialize_windows(self, windows: frozenset[str]) -> list[str]:
        # Sets iterate in hash-seed order; the config hash must not (2026-10-13, F-11).
        return sorted(windows)

    def evaluate(self, view: EvalView) -> bool:
        return bool(view.windows & self.windows)


class FloatLt(Condition):
    kind: Literal["float_lt"] = "float_lt"
    value: float

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.REGIME, Role.FILTER})

    def evaluate(self, view: EvalView) -> bool:
        ref = view.reference
        if ref is None or ref.free_float is None:
            return False
        return ref.free_float < self.value


class PriceBetween(Condition):
    kind: Literal["price_between"] = "price_between"
    min: float
    max: float

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.REGIME, Role.FILTER})

    def evaluate(self, view: EvalView) -> bool:
        return self.min <= view.bar.close <= self.max


class NoDilution(Condition):
    kind: Literal["no_dilution"] = "no_dilution"

    FAMILY: ClassVar[Family] = Family.CONTEXT
    ROLES: ClassVar[frozenset[Role]] = frozenset({Role.REGIME, Role.FILTER})

    def evaluate(self, view: EvalView) -> bool:
        ref = view.reference
        if ref is None:
            return False  # unknown dilution => fail closed (cannot assert "no dilution")
        return not ref.dilution_flag


# --------------------------------------------------------------------------- union

CONDITION_CLASSES: tuple[type[Condition], ...] = (
    EmaCrossUp, EmaCrossDown, MacdLineCrossUp, SupertrendState,
    MacdHistState, RsiLt, RsiGt,
    RelVolGt, PvtState, PvtBullishDivergence, VolumeExhaustion,
    VwapAbove, VwapBelow, FloatRotationGt,
    AdxGt, PsarState, PsarFlip, HodBreak, PremarketHighBreak,
    SessionWindowIn, FloatLt, PriceBetween, NoDilution,
    StopLossPct, TakeProfitPct, TrailingStopPct, TimeStopMinutes, SessionEnd,
)

AnyCondition = Annotated[
    Union[CONDITION_CLASSES],  # type: ignore[valid-type]
    Field(discriminator="kind"),
]

_CONDITION_ADAPTER: TypeAdapter = TypeAdapter(AnyCondition)


def parse_condition(data: object) -> Condition:
    """Parse one condition dict (or already-built Condition) via the discriminator."""
    if isinstance(data, Condition):
        return data
    return _CONDITION_ADAPTER.validate_python(data)
