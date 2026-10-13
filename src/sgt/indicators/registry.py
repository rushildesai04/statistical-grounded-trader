"""Indicator registry: spec validation, streaming builders, batch dispatch, and
the per-symbol :class:`IndicatorBank` consumed by the strategy layer.

``KINDS`` is the single source of truth for every indicator's parameter schema
(names, types, ranges, defaults) and output names. ``validate_spec`` enforces it
(raising ``ValueError``); ``build`` returns a streaming :class:`Indicator` and
``batch`` returns a vectorized :class:`pandas.DataFrame` with one column per
output aligned to the input rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from sgt.core.interfaces import Indicator, IndicatorSpec, ReferenceData
from sgt.core.types import Bar

from . import averages, momentum, trend, volatility, volume


@dataclass(frozen=True, slots=True)
class IndicatorContext:
    """Per-symbol reference facts some indicators need (e.g. free float)."""

    reference: ReferenceData | None = None


@dataclass(frozen=True, slots=True)
class ParamSpec:
    name: str
    type: type
    default: Any
    lo: float | None = None
    hi: float | None = None
    choices: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class KindInfo:
    kind: str
    params: tuple[ParamSpec, ...]
    outputs: tuple[str, ...]
    builder: Callable[[IndicatorSpec, dict, IndicatorContext | None], Indicator]
    batcher: Callable[[pd.DataFrame, dict, IndicatorContext | None], pd.DataFrame]
    cross_checks: tuple[Callable[[dict], None], ...] = ()


# --------------------------------------------------------------------------- cross-field validators


def _fast_lt_slow(p: dict) -> None:
    if p["fast"] >= p["slow"]:
        raise ValueError(f"fast ({p['fast']}) must be < slow ({p['slow']})")


def _psar_bounds(p: dict) -> None:
    start, step, mx = p["start"], p["step"], p["max"]
    if not (0.0 < start <= mx <= 1.0):
        raise ValueError(f"psar requires 0 < start <= max <= 1, got start={start}, max={mx}")
    if not (0.0 < step <= 1.0):
        raise ValueError(f"psar requires 0 < step <= 1, got step={step}")


# --------------------------------------------------------------------------- builders


def _b_sma(s, p, c):
    return averages.Sma(s, p["n"])


def _b_ema(s, p, c):
    return averages.Ema(s, p["n"])


def _b_ewo(s, p, c):
    return averages.Ewo(s, p["fast"], p["slow"])


def _b_rsi(s, p, c):
    return momentum.Rsi(s, p["n"])


def _b_macd(s, p, c):
    return momentum.Macd(s, p["fast"], p["slow"], p["signal"])


def _b_roc(s, p, c):
    return momentum.Roc(s, p["n"])


def _b_atr(s, p, c):
    return volatility.Atr(s, p["n"])


def _b_bbw(s, p, c):
    return volatility.Bbw(s, p["n"], p["k"])


def _b_psar(s, p, c):
    return trend.Psar(s, p["start"], p["step"], p["max"])


def _b_adx(s, p, c):
    return trend.Adx(s, p["n"])


def _b_supertrend(s, p, c):
    return trend.SuperTrend(s, p["n"], p["mult"])


def _b_cross(s, p, c):
    return trend.CrossStrength(s, p["fast"], p["slow"], p["atr_n"])


def _b_pvt(s, p, c):
    return volume.Pvt(s, p["signal_n"], p["signal_kind"])


def _b_vwap(s, p, c):
    return volume.Vwap(s, p["anchor"])


def _b_relvol(s, p, c):
    return volume.RelVol(s, p["lookback_sessions"])


def _b_float(s, p, c):
    ff = c.reference.free_float if (c is not None and c.reference is not None) else None
    return volume.FloatRotation(s, ff)


# --------------------------------------------------------------------------- registry table

KINDS: dict[str, KindInfo] = {
    "sma": KindInfo("sma", (ParamSpec("n", int, 20, 1, 1000),), ("value",), _b_sma, averages.sma_batch),
    "ema": KindInfo("ema", (ParamSpec("n", int, 9, 1, 500),), ("value",), _b_ema, averages.ema_batch),
    "ewo": KindInfo(
        "ewo", (ParamSpec("fast", int, 5, 1, 500), ParamSpec("slow", int, 35, 1, 500)),
        ("value",), _b_ewo, averages.ewo_batch, (_fast_lt_slow,),
    ),
    "rsi": KindInfo("rsi", (ParamSpec("n", int, 14, 2, 500),), ("value",), _b_rsi, momentum.rsi_batch),
    "macd": KindInfo(
        "macd",
        (ParamSpec("fast", int, 12, 1, 500), ParamSpec("slow", int, 26, 1, 500), ParamSpec("signal", int, 9, 1, 500)),
        ("line", "signal", "hist"), _b_macd, momentum.macd_batch, (_fast_lt_slow,),
    ),
    "roc": KindInfo("roc", (ParamSpec("n", int, 12, 1, 500),), ("value",), _b_roc, momentum.roc_batch),
    "atr": KindInfo("atr", (ParamSpec("n", int, 14, 1, 500),), ("value",), _b_atr, volatility.atr_batch),
    "bbw": KindInfo(
        "bbw", (ParamSpec("n", int, 20, 2, 500), ParamSpec("k", float, 2.0, 0.0, 10.0)),
        ("width", "percent_b"), _b_bbw, volatility.bbw_batch,
    ),
    "psar": KindInfo(
        "psar",
        (ParamSpec("start", float, 0.02, 0.0, 1.0), ParamSpec("step", float, 0.02, 0.0, 1.0),
         ParamSpec("max", float, 0.2, 0.0, 1.0)),
        ("sar", "trend"), _b_psar, trend.psar_batch, (_psar_bounds,),
    ),
    "adx": KindInfo("adx", (ParamSpec("n", int, 14, 2, 500),), ("adx", "plus_di", "minus_di"), _b_adx, trend.adx_batch),
    "supertrend": KindInfo(
        "supertrend", (ParamSpec("n", int, 10, 1, 500), ParamSpec("mult", float, 3.0, 0.0, 100.0)),
        ("line", "trend"), _b_supertrend, trend.supertrend_batch,
    ),
    "cross_strength": KindInfo(
        "cross_strength",
        (ParamSpec("fast", int, 9, 1, 500), ParamSpec("slow", int, 24, 1, 500), ParamSpec("atr_n", int, 14, 1, 500)),
        ("value",), _b_cross, trend.cross_strength_batch, (_fast_lt_slow,),
    ),
    "pvt": KindInfo(
        "pvt",
        (ParamSpec("signal_n", int, 14, 1, 500), ParamSpec("signal_kind", str, "sma", choices=("sma", "ema"))),
        ("pvt", "signal"), _b_pvt, volume.pvt_batch,
    ),
    "vwap": KindInfo(
        "vwap", (ParamSpec("anchor", str, "session", choices=("session", "rth")),),
        ("value",), _b_vwap, volume.vwap_batch,
    ),
    "relvol": KindInfo(
        "relvol", (ParamSpec("lookback_sessions", int, 20, 1, 250),),
        ("value", "cum"), _b_relvol, volume.relvol_batch,
    ),
    "float_rotation": KindInfo("float_rotation", (), ("value",), _b_float, volume.float_rotation_batch),
}


# --------------------------------------------------------------------------- validation


def _coerce(ps: ParamSpec, value: Any) -> Any:
    if ps.type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            else:
                raise ValueError(f"param {ps.name!r} must be an int, got {value!r}")
        if (ps.lo is not None and value < ps.lo) or (ps.hi is not None and value > ps.hi):
            raise ValueError(f"param {ps.name!r} must be in [{ps.lo}, {ps.hi}], got {value}")
        return value
    if ps.type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"param {ps.name!r} must be a number, got {value!r}")
        value = float(value)
        if (ps.lo is not None and value < ps.lo) or (ps.hi is not None and value > ps.hi):
            raise ValueError(f"param {ps.name!r} must be in [{ps.lo}, {ps.hi}], got {value}")
        return value
    if ps.type is str:
        if not isinstance(value, str):
            raise ValueError(f"param {ps.name!r} must be a string, got {value!r}")
        if ps.choices is not None and value not in ps.choices:
            raise ValueError(f"param {ps.name!r} must be one of {ps.choices}, got {value!r}")
        return value
    raise ValueError(f"unsupported param type for {ps.name!r}")  # pragma: no cover


def validate_spec(spec: IndicatorSpec) -> dict:
    """Validate a spec against ``KINDS`` and return the resolved parameter dict
    (defaults filled). Raises ``ValueError`` on unknown kind/param or out-of-range
    / wrong-type values."""
    info = KINDS.get(spec.kind)
    if info is None:
        raise ValueError(f"unknown indicator kind {spec.kind!r}; known: {sorted(KINDS)}")
    schema = {ps.name: ps for ps in info.params}
    provided = dict(spec.params)
    unknown = set(provided) - set(schema)
    if unknown:
        raise ValueError(f"{spec.kind!r} got unknown param(s) {sorted(unknown)}; allowed: {sorted(schema)}")
    resolved: dict[str, Any] = {}
    for name, ps in schema.items():
        resolved[name] = _coerce(ps, provided[name]) if name in provided else ps.default
    for check in info.cross_checks:
        check(resolved)
    return resolved


# --------------------------------------------------------------------------- public API


def build(spec: IndicatorSpec, ctx: IndicatorContext | None = None) -> Indicator:
    params = validate_spec(spec)
    return KINDS[spec.kind].builder(spec, params, ctx)


def batch(spec: IndicatorSpec, frame: pd.DataFrame, ctx: IndicatorContext | None = None) -> pd.DataFrame:
    params = validate_spec(spec)
    return KINDS[spec.kind].batcher(frame, params, ctx)


_FRAME_COLUMNS = ("ts", "open", "high", "low", "close", "volume", "session_date", "minute_of_day", "segment")


def frame_from_bars(bars: Sequence[Bar]) -> pd.DataFrame:
    """Build the research DataFrame the batch functions expect from a bar list."""
    return pd.DataFrame(
        {
            "ts": [b.ts for b in bars],
            "open": [b.open for b in bars],
            "high": [b.high for b in bars],
            "low": [b.low for b in bars],
            "close": [b.close for b in bars],
            "volume": [b.volume for b in bars],
            "session_date": [b.session_date for b in bars],
            "minute_of_day": [b.minute_of_day for b in bars],
            "segment": [b.segment for b in bars],
        },
        columns=list(_FRAME_COLUMNS),
    )


class IndicatorBank:
    """Holds the built streaming indicators for one symbol, keyed by ``spec.key``.

    ``update(bar)`` advances every indicator; ``snapshot()`` returns the flat
    ``{"<spec.key>.<output>": value|None}`` mapping the strategy layer consumes."""

    def __init__(self, specs: Sequence[IndicatorSpec], ctx: IndicatorContext | None = None) -> None:
        self._inds: dict[str, Indicator] = {}
        for spec in specs:
            if spec.key in self._inds:
                raise ValueError(f"duplicate indicator spec {spec.key!r} in bank")
            self._inds[spec.key] = build(spec, ctx)
        self._order = list(self._inds.values())

    def update(self, bar: Bar) -> None:
        for ind in self._order:
            ind.update(bar)

    def snapshot(self) -> dict[str, float | None]:
        out: dict[str, float | None] = {}
        for key, ind in self._inds.items():
            vals = ind.values()
            for o in ind.outputs:
                out[f"{key}.{o}"] = vals[o]
        return out

    def reset(self) -> None:
        for ind in self._order:
            ind.reset()

    def __getitem__(self, key: str) -> Indicator:
        return self._inds[key]

    def __len__(self) -> int:
        return len(self._inds)

    def __contains__(self, key: str) -> bool:
        return key in self._inds
