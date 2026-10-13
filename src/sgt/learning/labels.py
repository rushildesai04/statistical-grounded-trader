"""Triple-barrier labeling for meta-labeling (plan §9.1).

Each rule-generated candidate is labeled by the first of three barriers it hits,
measured on the bars that follow the entry:

    +1  take-profit touched first (candidate "worked")
    -1  stop touched first (candidate "failed")
     0  neither touched within the time limit (timed out)

Barriers are percentages of the entry price (``3.0`` == 3%), matching the
strategy-config convention (``stop_loss_pct: 3.0`` in ``trader_v0``) so labels
describe exactly what the strategy would have experienced.

Determinism and conservatism
- Only bars strictly after ``entry_ts`` are used (no look-ahead on the entry bar).
- When a single bar's range touches BOTH barriers we cannot know the intrabar
  order from OHLC alone. ``same_bar_policy="stop_first"`` (default) resolves the
  ambiguity conservatively as a stop; ``"tp_first"`` is offered for sensitivity
  analysis only.
- Gap-through: if a bar OPENS already beyond a barrier, the fill is modeled at
  that open, not the barrier level (you could not have filled at the barrier).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Sequence

import numpy as np
import pandas as pd

from sgt.core.types import Bar

_SAME_BAR_POLICIES = ("stop_first", "tp_first")


class InsufficientBarsError(ValueError):
    """Raised when the post-entry horizon is too short to assign a label.

    This is the "lacking future bars" case: no barrier was touched and fewer than
    ``max_bars`` bars exist after the entry, so neither a hit nor a clean timeout
    can be asserted. ``label_candidates`` catches this and emits ``NaN``.
    """


@dataclass(frozen=True, slots=True)
class BarrierOutcome:
    label: int          # +1 take-profit, -1 stop, 0 timeout
    exit_ts: datetime
    exit_price: float
    bars_held: int      # number of post-entry bars until exit (>= 1)
    ret: float          # (exit_price - entry_price) / entry_price
    mfe: float          # max favorable excursion over held bars (fractional, >= 0 typ.)
    mae: float          # max adverse excursion over held bars (fractional, <= 0 typ.)


def triple_barrier(
    entry_ts: datetime,
    entry_price: float,
    bars_after: Sequence[Bar],
    take_profit_pct: float,
    stop_pct: float,
    max_bars: int,
    *,
    same_bar_policy: str = "stop_first",
) -> BarrierOutcome:
    """Label one candidate. See module docstring for semantics.

    ``bars_after`` may contain bars at or before ``entry_ts``; they are filtered
    out here so callers can pass a symbol's full bar list. ``take_profit_pct`` and
    ``stop_pct`` are positive percentages of ``entry_price``.
    """
    if same_bar_policy not in _SAME_BAR_POLICIES:
        raise ValueError(f"same_bar_policy must be one of {_SAME_BAR_POLICIES}, got {same_bar_policy!r}")
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")
    if take_profit_pct <= 0 or stop_pct <= 0:
        raise ValueError("take_profit_pct and stop_pct must be > 0 (percent units)")
    if max_bars <= 0:
        raise ValueError(f"max_bars must be > 0, got {max_bars}")

    future = [b for b in bars_after if b.ts > entry_ts]
    if not future:
        raise InsufficientBarsError("no bars strictly after entry_ts")
    horizon = future[: max_bars]

    tp_price = entry_price * (1.0 + take_profit_pct / 100.0)
    stop_price = entry_price * (1.0 - stop_pct / 100.0)

    mfe = 0.0
    mae = 0.0
    for i, bar in enumerate(horizon):
        mfe = max(mfe, (bar.high - entry_price) / entry_price)
        mae = min(mae, (bar.low - entry_price) / entry_price)
        bars_held = i + 1

        # Gap-through at the open takes precedence: the barrier was unreachable
        # at its level, so fill at the open the market actually printed.
        if bar.open >= tp_price:
            return _outcome(1, bar, bar.open, entry_price, bars_held, mfe, mae)
        if bar.open <= stop_price:
            return _outcome(-1, bar, bar.open, entry_price, bars_held, mfe, mae)

        hit_tp = bar.high >= tp_price
        hit_stop = bar.low <= stop_price
        if hit_tp and hit_stop:
            if same_bar_policy == "stop_first":
                return _outcome(-1, bar, stop_price, entry_price, bars_held, mfe, mae)
            return _outcome(1, bar, tp_price, entry_price, bars_held, mfe, mae)
        if hit_tp:
            return _outcome(1, bar, tp_price, entry_price, bars_held, mfe, mae)
        if hit_stop:
            return _outcome(-1, bar, stop_price, entry_price, bars_held, mfe, mae)

    # No barrier touched within the bars we have.
    if len(future) < max_bars:
        # Horizon incomplete -> label is undetermined, not a timeout.
        raise InsufficientBarsError(
            f"only {len(future)} post-entry bars available, need {max_bars} for a timeout label"
        )
    last = horizon[-1]
    return _outcome(0, last, last.close, entry_price, len(horizon), mfe, mae)


def _outcome(
    label: int, bar: Bar, exit_price: float, entry_price: float, bars_held: int, mfe: float, mae: float
) -> BarrierOutcome:
    return BarrierOutcome(
        label=label,
        exit_ts=bar.ts,
        exit_price=float(exit_price),
        bars_held=bars_held,
        ret=(exit_price - entry_price) / entry_price,
        mfe=float(mfe),
        mae=float(mae),
    )


_LABEL_COLUMNS = ("label", "exit_ts", "exit_price", "bars_held", "ret", "mfe", "mae")


def label_candidates(
    candidates: pd.DataFrame,
    bars_lookup: Callable[[str], Sequence[Bar]],
    take_profit_pct: float,
    stop_pct: float,
    max_bars: int,
    *,
    same_bar_policy: str = "stop_first",
    symbol_col: str = "symbol",
    ts_col: str = "ts",
    price_col: str = "entry_price",
) -> pd.DataFrame:
    """Add triple-barrier label columns to a candidates frame.

    ``bars_lookup(symbol)`` returns that symbol's bars (any order; filtered to
    strictly-after-entry internally). Rows with too short a post-entry horizon get
    ``label = NaN`` and NaN excursions; the count of such rows is
    ``out["label"].isna().sum()``. The input frame is not mutated.
    """
    for col in (symbol_col, ts_col, price_col):
        if col not in candidates.columns:
            raise KeyError(f"candidates is missing required column {col!r}")

    rows: list[dict] = []
    cache: dict[str, Sequence[Bar]] = {}
    for row in candidates.itertuples(index=False):
        symbol = getattr(row, symbol_col)
        entry_ts = getattr(row, ts_col)
        entry_price = float(getattr(row, price_col))
        if symbol not in cache:
            cache[symbol] = bars_lookup(symbol)
        try:
            out = triple_barrier(
                entry_ts, entry_price, cache[symbol], take_profit_pct, stop_pct, max_bars,
                same_bar_policy=same_bar_policy,
            )
            rows.append({
                "label": float(out.label), "exit_ts": out.exit_ts, "exit_price": out.exit_price,
                "bars_held": out.bars_held, "ret": out.ret, "mfe": out.mfe, "mae": out.mae,
            })
        except InsufficientBarsError:
            rows.append({c: np.nan for c in _LABEL_COLUMNS})

    labels = pd.DataFrame(rows, index=candidates.index, columns=list(_LABEL_COLUMNS))
    return pd.concat([candidates.copy(), labels], axis=1)
