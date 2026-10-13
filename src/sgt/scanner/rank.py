"""Scanner: point-in-time watchlist construction (docs/IMPLEMENTATION_PLAN.md §12).

``Scanner.scan`` turns a set of candidate symbols (each with its bar history up to and
through the current session) into a ranked, size-capped watchlist. Everything is
computed strictly point-in-time at time ``t``:

- only bars that OPENED before ``t`` are used (a 1-minute bar is included once ``t`` has
  reached its close), so a bar stamped at/after ``t`` can never influence the result;
- relative volume uses the ``cum`` output of an :class:`IndicatorBank` relvol indicator
  fed the symbol's completed bars — prior sessions form the baseline, the current
  session is measured against it;
- gap% is the current session's first bar open vs the prior session's last RTH close;
- reference facts (float, dilution) and crowd attention are supplied only through
  injected callables, so the scanner itself performs no I/O and is fully deterministic.

Hard eligibility is delegated to :class:`UniverseFilter`. Telegram-alerted symbols are
always scored (an alert-recency term) but must still clear every hard filter — an alert
never bypasses float/price/dilution/volume gates. Ties in score break by symbol
(ascending) so the output order is deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Mapping, Sequence

from sgt.core.interfaces import IndicatorSpec, ReferenceData
from sgt.core.types import Bar, Segment
from sgt.indicators.registry import IndicatorBank
from sgt.scanner.universe import (
    F_CUM_REL_VOL,
    F_DILUTION,
    F_DOLLAR_VOLUME,
    F_FREE_FLOAT,
    F_GAP_PCT,
    F_PRICE,
    UniverseFilter,
)

DEFAULT_MAX_WATCHLIST = 20
DEFAULT_RELVOL_LOOKBACK = 20

# Deterministic score = weighted sum of the terms below (documented, no hidden tuning).
# log rel-vol dominates (volume is the primary low-float runner signal); gap% and float
# rotation add momentum/rotation context; attention z and alert recency fold in crowd
# pressure without letting it trigger (§12: crowd signals rank, never trigger).
DEFAULT_WEIGHTS: dict[str, float] = {
    "log_rel_vol": 1.0,
    "gap_pct": 0.1,
    "float_rotation": 0.5,
    "attention_z": 0.3,
    "alert_recency": 0.5,
}


@dataclass(frozen=True, slots=True)
class WatchItem:
    """One ranked watchlist entry. ``features`` is the point-in-time snapshot used."""

    symbol: str
    score: float
    reasons: tuple[str, ...]
    features: dict[str, float | bool | None] = field(default_factory=dict)


def _require_utc(name: str, ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{name} must be tz-aware UTC, got {ts!r}")


class Scanner:
    def __init__(
        self,
        filt: UniverseFilter,
        *,
        max_watchlist: int = DEFAULT_MAX_WATCHLIST,
        relvol_lookback: int = DEFAULT_RELVOL_LOOKBACK,
        weights: Mapping[str, float] | None = None,
    ) -> None:
        if max_watchlist <= 0:
            raise ValueError("max_watchlist must be > 0")
        self._filter = filt
        self._max = int(max_watchlist)
        self._relvol_spec = IndicatorSpec.of("relvol", lookback_sessions=int(relvol_lookback))
        self._weights = dict(DEFAULT_WEIGHTS)
        if weights is not None:
            self._weights.update(weights)

    # ------------------------------------------------------------------ public API
    def scan(
        self,
        t: datetime,
        candidates: Mapping[str, Sequence[Bar]],
        *,
        reference: Callable[[str, date], ReferenceData],
        attention: Callable[[str, datetime], Mapping[str, float]] | None = None,
        alert_symbols: set[str] | frozenset[str] = frozenset(),
    ) -> list[WatchItem]:
        _require_utc("scan t", t)
        alerts = {s.upper() for s in alert_symbols}
        items: list[WatchItem] = []
        for symbol, bars in candidates.items():
            sym = symbol.upper()
            feats = self._features(sym, bars, t, reference, attention, sym in alerts)
            if feats is None:
                continue
            if self._filter.evaluate(feats):  # non-empty == failed a hard filter
                continue
            score = self._score(feats)
            items.append(WatchItem(sym, score, self._reasons(feats), feats))
        items.sort(key=lambda w: (-w.score, w.symbol))
        return items[: self._max]

    # ------------------------------------------------------------------ features
    def _features(
        self,
        symbol: str,
        bars: Sequence[Bar],
        t: datetime,
        reference: Callable[[str, date], ReferenceData],
        attention: Callable[[str, datetime], Mapping[str, float]] | None,
        alerted: bool,
    ) -> dict[str, float | bool | None] | None:
        completed = sorted((b for b in bars if b.ts < t), key=lambda b: b.ts)
        if not completed:
            return None
        session_date = completed[-1].session_date
        cur_bars = [b for b in completed if b.session_date == session_date]
        if not cur_bars:
            return None

        price = cur_bars[-1].close
        cum_volume = sum(b.volume for b in cur_bars)
        dollar_volume = sum(b.close * b.volume for b in cur_bars)

        ref = reference(symbol, session_date)
        free_float = ref.free_float if ref is not None else None
        dilution = bool(ref.dilution_flag) if ref is not None else False

        cum_rel_vol = self._cum_rel_vol(completed)
        gap_pct = self._gap_pct(completed, cur_bars, session_date)

        attention_z = 0.0
        if attention is not None:
            att = attention(symbol, t) or {}
            attention_z = float(att.get("mention_velocity_z", 0.0))

        float_rotation = 0.0
        if isinstance(free_float, (int, float)) and free_float > 0.0:
            float_rotation = cum_volume / free_float

        return {
            F_PRICE: price,
            F_FREE_FLOAT: free_float,
            F_CUM_REL_VOL: cum_rel_vol,
            F_GAP_PCT: gap_pct,
            F_DOLLAR_VOLUME: dollar_volume,
            F_DILUTION: dilution,
            "cum_session_volume": cum_volume,
            "float_rotation": float_rotation,
            "attention_z": attention_z,
            "alert_recency": 1.0 if alerted else 0.0,
        }

    def _cum_rel_vol(self, completed: Sequence[Bar]) -> float | None:
        bank = IndicatorBank([self._relvol_spec])
        for b in completed:
            bank.update(b)
        val = bank[self._relvol_spec.key].values().get("cum")
        return float(val) if val is not None else None

    @staticmethod
    def _gap_pct(
        completed: Sequence[Bar], cur_bars: Sequence[Bar], session_date: date
    ) -> float | None:
        prior_dates = {b.session_date for b in completed if b.session_date < session_date}
        if not prior_dates:
            return None
        prior = max(prior_dates)
        prior_rth = [b for b in completed if b.session_date == prior and b.segment is Segment.RTH]
        if not prior_rth:
            return None
        prior_close = prior_rth[-1].close
        if prior_close <= 0.0:
            return None
        first_open = cur_bars[0].open
        return (first_open - prior_close) / prior_close * 100.0

    # ------------------------------------------------------------------ scoring
    def _score(self, feats: Mapping[str, float | bool | None]) -> float:
        w = self._weights
        cum_rv = feats.get(F_CUM_REL_VOL)
        log_rv = math.log(cum_rv) if isinstance(cum_rv, (int, float)) and cum_rv > 0.0 else 0.0
        gap = feats.get(F_GAP_PCT)
        gap_v = float(gap) if isinstance(gap, (int, float)) else 0.0
        return (
            w["log_rel_vol"] * log_rv
            + w["gap_pct"] * gap_v
            + w["float_rotation"] * float(feats.get("float_rotation") or 0.0)
            + w["attention_z"] * float(feats.get("attention_z") or 0.0)
            + w["alert_recency"] * float(feats.get("alert_recency") or 0.0)
        )

    @staticmethod
    def _reasons(feats: Mapping[str, float | bool | None]) -> tuple[str, ...]:
        out: list[str] = []
        cum_rv = feats.get(F_CUM_REL_VOL)
        if isinstance(cum_rv, (int, float)):
            out.append(f"rel_vol={cum_rv:.2f}")
        gap = feats.get(F_GAP_PCT)
        if isinstance(gap, (int, float)):
            out.append(f"gap={gap:.1f}%")
        fr = feats.get("float_rotation")
        if isinstance(fr, (int, float)) and fr > 0.0:
            out.append(f"float_rot={fr:.2f}")
        if feats.get("alert_recency"):
            out.append("alerted")
        if feats.get(F_DILUTION):
            out.append("dilution")
        return tuple(out)
