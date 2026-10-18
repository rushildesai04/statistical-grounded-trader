"""Point-in-time feature vector for the decision model (docs/DECISION_MODEL.md §4).

Every feature is computed strictly from data knowable at the signal bar's close (the
current and previous :class:`IndicatorBank` snapshots, a bounded ring buffer of prior
bars/snapshots, the point-in-time :class:`ReferenceData`, and the round-trip cost at the
decision); nothing reads a bar after ``t``. Features are scale-free (ATR/price-normalized,
logs, one-hots, bounded oscillators), named ``f_*``, and ``NaN`` when an input is missing.
``q_float_degraded`` is the per-row float-quality flag (§10 F3). The set is declared once in
:data:`FEATURE_SPECS`; :func:`feature_indicator_specs` derives the specs so the dataset
builder and :class:`sgt.decision.runtime.DecisionRuntime` build identical banks.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Protocol, runtime_checkable

from sgt.brokers.costs import CostModel
from sgt.catalysts.reference import FLOAT_PROXY_SOURCE
from sgt.core.interfaces import IndicatorSpec, ReferenceData
from sgt.core.types import Bar, Segment, Side
from sgt.strategy.base import HistoryView
from sgt.decision.triggers import TRIGGER_NAMES

NaN = float("nan")
_PVT_WINDOW = 30  # bars for the PVT z-score / slope normalization
# Prior bars a feature may read (PVT window needs 29 prior snaps; EMA slope 3). The
# dataset builder and DecisionRuntime size their ring buffers to this.
FEATURE_HISTORY = _PVT_WINDOW

# --------------------------------------------------------------------------- indicator specs

SPEC_EMA_FAST = IndicatorSpec.of("ema", n=9)
SPEC_EMA_SLOW = IndicatorSpec.of("ema", n=24)
SPEC_ATR = IndicatorSpec.of("atr", n=14)
SPEC_MACD = IndicatorSpec.of("macd", fast=12, slow=26, signal=9)
SPEC_PSAR = IndicatorSpec.of("psar")
SPEC_SUPERTREND = IndicatorSpec.of("supertrend", n=10, mult=3.0)
SPEC_ADX = IndicatorSpec.of("adx", n=14)
SPEC_RSI = IndicatorSpec.of("rsi", n=14)
SPEC_ROC5 = IndicatorSpec.of("roc", n=5)
SPEC_ROC15 = IndicatorSpec.of("roc", n=15)
SPEC_EWO = IndicatorSpec.of("ewo", fast=5, slow=35)
SPEC_RELVOL = IndicatorSpec.of("relvol", lookback_sessions=20)
SPEC_PVT = IndicatorSpec.of("pvt", signal_n=14, signal_kind="sma")
SPEC_VWAP = IndicatorSpec.of("vwap", anchor="session")
SPEC_BBW = IndicatorSpec.of("bbw", n=20, k=2.0)
SPEC_FLOAT_ROT = IndicatorSpec.of("float_rotation")

_ALL_SPECS: tuple[IndicatorSpec, ...] = (
    SPEC_EMA_FAST, SPEC_EMA_SLOW, SPEC_ATR, SPEC_MACD, SPEC_PSAR, SPEC_SUPERTREND,
    SPEC_ADX, SPEC_RSI, SPEC_ROC5, SPEC_ROC15, SPEC_EWO, SPEC_RELVOL, SPEC_PVT,
    SPEC_VWAP, SPEC_BBW, SPEC_FLOAT_ROT,
)

_K_EMA_FAST = f"{SPEC_EMA_FAST.key}.value"
_K_EMA_SLOW = f"{SPEC_EMA_SLOW.key}.value"
_K_ATR = f"{SPEC_ATR.key}.value"
_K_MACD_HIST = f"{SPEC_MACD.key}.hist"
_K_PSAR = f"{SPEC_PSAR.key}.sar"
_K_ST_TREND = f"{SPEC_SUPERTREND.key}.trend"
_K_ADX = f"{SPEC_ADX.key}.adx"
_K_RSI = f"{SPEC_RSI.key}.value"
_K_ROC5 = f"{SPEC_ROC5.key}.value"
_K_ROC15 = f"{SPEC_ROC15.key}.value"
_K_EWO = f"{SPEC_EWO.key}.value"
_K_RELVOL = f"{SPEC_RELVOL.key}.value"
_K_RELVOL_CUM = f"{SPEC_RELVOL.key}.cum"
_K_PVT = f"{SPEC_PVT.key}.pvt"
_K_PVT_SIG = f"{SPEC_PVT.key}.signal"
_K_VWAP = f"{SPEC_VWAP.key}.value"
_K_BBW = f"{SPEC_BBW.key}.width"
_K_FLOAT_ROT = f"{SPEC_FLOAT_ROT.key}.value"


def feature_indicator_specs() -> list[IndicatorSpec]:
    """De-duplicated indicator specs every feature reads (bank construction)."""
    seen: dict[str, IndicatorSpec] = {}
    for s in _ALL_SPECS:
        seen.setdefault(s.key, s)
    return list(seen.values())


# --------------------------------------------------------------------------- cost source

@runtime_checkable
class CostSource(Protocol):
    """Round-trip cost provider (``sgt.brokers.measured_costs.MeasuredCostModel`` is
    one). Returns ``(round_trip_fraction, source)`` for a symbol/timestamp/price."""

    def round_trip(self, symbol: str, ts: datetime, price: float) -> tuple[float, str]: ...


@dataclass(frozen=True, slots=True)
class CostModelAdapter:
    """Adapts a bucket :class:`CostModel` to the :class:`CostSource` interface so the
    builder/runtime work before measured spreads exist (source tag ``"model"``)."""

    model: CostModel = CostModel()
    tag: str = "model"   # "calibrated" when the buckets are fitted to measured spreads

    def round_trip(self, symbol: str, ts: datetime, price: float) -> tuple[float, str]:
        if price <= 0:
            raise ValueError("round_trip price must be > 0")
        return 2.0 * self.model.half_spread_pct(price), self.tag


# --------------------------------------------------------------------------- context

@dataclass(frozen=True, slots=True)
class FeatureContext:
    """Everything a feature may read at one bar close (all point-in-time)."""

    bar: Bar
    snap: Mapping[str, float | None]
    snap_prev: Mapping[str, float | None] | None
    history: HistoryView
    reference: ReferenceData | None
    cost_frac: float | None               # round-trip cost fraction at the decision
    session_high_prior: float | None      # max high of PRE+RTH bars before this one
    premarket_high: float | None
    bars_since_session_high: int | None   # 0 if this bar set the session high
    trigger: str | None                   # the trigger that fired (one-hot source)
    windows: frozenset[str]

    def now(self, key: str) -> float | None:
        return self.snap.get(key)

    def prev(self, key: str) -> float | None:
        return self.snap_prev.get(key) if self.snap_prev is not None else None

    def hist_snap(self, back: int, key: str) -> float | None:
        """Value ``back`` bars ago (1 == previous), or None if unavailable."""
        n = len(self.history)
        if back < 1 or back > n:
            return None
        return self.history.snaps[n - back].get(key)


# --------------------------------------------------------------------------- helpers

def _atr(ctx: FeatureContext) -> float | None:
    a = ctx.now(_K_ATR)
    return a if (a is not None and a > 0.0) else None


def _over_atr(value: float | None, ctx: FeatureContext) -> float:
    a = _atr(ctx)
    if value is None or a is None:
        return NaN
    return value / a


def _safe_log(x: float | None) -> float:
    return math.log(x) if (x is not None and x > 0.0) else NaN


def _pvt_diff_series(ctx: FeatureContext) -> list[float]:
    """(pvt - signal) over the last ``_PVT_WINDOW`` bars, oldest first (point-in-time)."""
    out: list[float] = []
    n = len(ctx.history)
    for i in range(max(0, n - (_PVT_WINDOW - 1)), n):
        s = ctx.history.snaps[i]
        p, sig = s.get(_K_PVT), s.get(_K_PVT_SIG)
        if p is not None and sig is not None:
            out.append(p - sig)
    cur_p, cur_sig = ctx.now(_K_PVT), ctx.now(_K_PVT_SIG)
    if cur_p is not None and cur_sig is not None:
        out.append(cur_p - cur_sig)
    return out


def _pvt_level_series(ctx: FeatureContext) -> list[float]:
    out: list[float] = []
    n = len(ctx.history)
    for i in range(max(0, n - (_PVT_WINDOW - 1)), n):
        p = ctx.history.snaps[i].get(_K_PVT)
        if p is not None:
            out.append(p)
    cur = ctx.now(_K_PVT)
    if cur is not None:
        out.append(cur)
    return out


def _std(xs: Sequence[float]) -> float | None:
    k = len(xs)
    if k < 2:
        return None
    mean = sum(xs) / k
    var = sum((x - mean) ** 2 for x in xs) / k
    return math.sqrt(var) if var > 0.0 else None


# --------------------------------------------------------------------------- feature fns

def _f_ema_spread_atr(ctx):
    f, s = ctx.now(_K_EMA_FAST), ctx.now(_K_EMA_SLOW)
    return _over_atr(f - s if (f is not None and s is not None) else None, ctx)


def _f_ema_slope_atr(ctx):
    now, past = ctx.now(_K_EMA_FAST), ctx.hist_snap(3, _K_EMA_FAST)
    return _over_atr((now - past) / 3.0 if (now is not None and past is not None) else None, ctx)


def _f_macd_hist_atr(ctx):
    return _over_atr(ctx.now(_K_MACD_HIST), ctx)


def _f_macd_hist_delta_atr(ctx):
    now, prev = ctx.now(_K_MACD_HIST), ctx.prev(_K_MACD_HIST)
    return _over_atr(now - prev if (now is not None and prev is not None) else None, ctx)


def _f_psar_dist_atr(ctx):
    sar = ctx.now(_K_PSAR)
    return _over_atr(ctx.bar.close - sar if sar is not None else None, ctx)


def _f_supertrend_side(ctx):
    t = ctx.now(_K_ST_TREND)
    return NaN if t is None else (1.0 if t > 0 else -1.0)


def _f_adx(ctx):
    v = ctx.now(_K_ADX)
    return NaN if v is None else v


def _f_rsi(ctx):
    v = ctx.now(_K_RSI)
    return NaN if v is None else v


def _f_roc5(ctx):
    v = ctx.now(_K_ROC5)
    return NaN if v is None else v


def _f_roc15(ctx):
    v = ctx.now(_K_ROC15)
    return NaN if v is None else v


def _f_ewo_price(ctx):
    v = ctx.now(_K_EWO)
    c = ctx.bar.close
    return v / c if (v is not None and c > 0.0) else NaN


def _f_rel_vol_bar_log(ctx):
    return _safe_log(ctx.now(_K_RELVOL))


def _f_rel_vol_cum_log(ctx):
    return _safe_log(ctx.now(_K_RELVOL_CUM))


def _f_pvt_minus_signal_z(ctx):
    series = _pvt_diff_series(ctx)
    if not series:
        return NaN
    std = _std(series)
    if std is None:
        return NaN
    mean = sum(series) / len(series)
    return (series[-1] - mean) / std


def _f_pvt_slope(ctx):
    now, prev = ctx.now(_K_PVT), ctx.prev(_K_PVT)
    if now is None or prev is None:
        return NaN
    std = _std(_pvt_level_series(ctx))
    if std is None:
        return NaN
    return (now - prev) / std


def _f_close_vwap_atr(ctx):
    vwap = ctx.now(_K_VWAP)
    if vwap is None:
        vwap = ctx.bar.vwap
    return _over_atr(ctx.bar.close - vwap if vwap else None, ctx)


def _f_float_rotation_log(ctx):
    return _safe_log(ctx.now(_K_FLOAT_ROT))


def _f_atr_price(ctx):
    a, c = ctx.now(_K_ATR), ctx.bar.close
    return a / c if (a is not None and c > 0.0) else NaN


def _f_bb_width(ctx):
    v = ctx.now(_K_BBW)
    return NaN if v is None else v


def _f_close_hod_atr(ctx):
    hi = ctx.session_high_prior
    return _over_atr(ctx.bar.close - hi if hi else None, ctx)


def _f_close_pmh_atr(ctx):
    hi = ctx.premarket_high
    return _over_atr(ctx.bar.close - hi if hi else None, ctx)


def _f_bars_since_hod(ctx):
    return NaN if ctx.bars_since_session_high is None else float(ctx.bars_since_session_high)


def _f_minute_sin(ctx):
    return math.sin(2.0 * math.pi * ctx.bar.minute_of_day / 1440.0)


def _f_minute_cos(ctx):
    return math.cos(2.0 * math.pi * ctx.bar.minute_of_day / 1440.0)


def _f_win_opening(ctx):
    return 1.0 if "opening_hour" in ctx.windows else 0.0


def _f_win_midday(ctx):
    return 1.0 if "midday" in ctx.windows else 0.0


def _f_win_power(ctx):
    return 1.0 if "power_hour" in ctx.windows else 0.0


def _f_log_price(ctx):
    return _safe_log(ctx.bar.close)


def _f_log_float(ctx):
    ref = ctx.reference
    return _safe_log(ref.free_float) if (ref is not None and ref.free_float) else NaN


def _f_gap_pct(ctx):
    closes = ctx.history.closes
    if not len(closes):
        return NaN
    prev_close = closes[-1]
    return (ctx.bar.open - prev_close) / prev_close * 100.0 if prev_close else NaN


def _f_dilution_flag(ctx):
    ref = ctx.reference
    return NaN if ref is None else (1.0 if ref.dilution_flag else 0.0)


def _f_cost_rt_pct(ctx):
    return NaN if ctx.cost_frac is None else ctx.cost_frac * 100.0


def _seg_onehot(ctx, seg):
    return 1.0 if ctx.bar.segment is seg else 0.0


def _trig_onehot(name):
    def fn(ctx):
        return 1.0 if ctx.trigger == name else 0.0
    return fn


# --------------------------------------------------------------------------- registry

@dataclass(frozen=True, slots=True)
class FeatureSpec:
    name: str
    fn: Callable[[FeatureContext], float]
    float_dependent: bool = False


_TRIGGER_FEATURE_NAMES = {
    "ema_cross_up": "f_trig_ema_cross_up",
    "macd_cross_up": "f_trig_macd_cross_up",
    "vwap_reclaim": "f_trig_vwap_reclaim",
    "hod_break": "f_trig_hod_break",
    "pmh_break": "f_trig_pmh_break",
    "psar_flip_up": "f_trig_psar_flip_up",
}

FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("f_ema_spread_atr", _f_ema_spread_atr),
    FeatureSpec("f_ema_slope_atr", _f_ema_slope_atr),
    FeatureSpec("f_macd_hist_atr", _f_macd_hist_atr),
    FeatureSpec("f_macd_hist_delta_atr", _f_macd_hist_delta_atr),
    FeatureSpec("f_psar_dist_atr", _f_psar_dist_atr),
    FeatureSpec("f_supertrend_side", _f_supertrend_side),
    FeatureSpec("f_adx", _f_adx),
    FeatureSpec("f_rsi", _f_rsi),
    FeatureSpec("f_roc5", _f_roc5),
    FeatureSpec("f_roc15", _f_roc15),
    FeatureSpec("f_ewo_price", _f_ewo_price),
    FeatureSpec("f_rel_vol_bar_log", _f_rel_vol_bar_log),
    FeatureSpec("f_rel_vol_cum_log", _f_rel_vol_cum_log),
    FeatureSpec("f_pvt_minus_signal_z", _f_pvt_minus_signal_z),
    FeatureSpec("f_pvt_slope", _f_pvt_slope),
    FeatureSpec("f_close_vwap_atr", _f_close_vwap_atr),
    FeatureSpec("f_float_rotation_log", _f_float_rotation_log, float_dependent=True),
    FeatureSpec("f_atr_price", _f_atr_price),
    FeatureSpec("f_bb_width", _f_bb_width),
    FeatureSpec("f_close_hod_atr", _f_close_hod_atr),
    FeatureSpec("f_close_pmh_atr", _f_close_pmh_atr),
    FeatureSpec("f_bars_since_hod", _f_bars_since_hod),
    FeatureSpec("f_minute_sin", _f_minute_sin),
    FeatureSpec("f_minute_cos", _f_minute_cos),
    FeatureSpec("f_win_opening", _f_win_opening),
    FeatureSpec("f_win_midday", _f_win_midday),
    FeatureSpec("f_win_power", _f_win_power),
    FeatureSpec("f_log_price", _f_log_price),
    FeatureSpec("f_log_float", _f_log_float, float_dependent=True),
    FeatureSpec("f_gap_pct", _f_gap_pct),
    FeatureSpec("f_dilution_flag", _f_dilution_flag),
    FeatureSpec("f_cost_rt_pct", _f_cost_rt_pct),
    FeatureSpec("f_seg_pre", lambda ctx: _seg_onehot(ctx, Segment.PRE)),
    FeatureSpec("f_seg_rth", lambda ctx: _seg_onehot(ctx, Segment.RTH)),
    FeatureSpec("f_seg_post", lambda ctx: _seg_onehot(ctx, Segment.POST)),
    *tuple(FeatureSpec(_TRIGGER_FEATURE_NAMES[t], _trig_onehot(t)) for t in TRIGGER_NAMES),
)

FEATURE_NAMES: tuple[str, ...] = tuple(fs.name for fs in FEATURE_SPECS)
FLOAT_DEPENDENT_FEATURES: frozenset[str] = frozenset(
    fs.name for fs in FEATURE_SPECS if fs.float_dependent)


def float_degraded(reference: ReferenceData | None) -> bool:
    """§10 F3 quality flag: vendor snapshot good; EDGAR proxy / dilution / unknown
    float are degraded."""
    if reference is None or reference.free_float is None:
        return True
    return reference.float_source == FLOAT_PROXY_SOURCE or reference.dilution_flag


def compute_features(ctx: FeatureContext) -> dict[str, float]:
    """The full ``{f_*: value}`` vector plus ``q_float_degraded`` for one bar close."""
    out = {fs.name: float(fs.fn(ctx)) for fs in FEATURE_SPECS}
    out["q_float_degraded"] = 1.0 if float_degraded(ctx.reference) else 0.0
    return out
