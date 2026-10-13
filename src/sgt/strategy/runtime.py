"""Per-symbol strategy runtime: the FLAT -> ARMED -> ENTRY_PENDING -> IN_POSITION
-> EXIT_PENDING -> FLAT state machine with a HALTED overlay (plan §7.3).

Decisions are produced ONLY on bar close; the executor turns them into orders that
can fill no earlier than the next bar, so the runtime never looks ahead. Every bar
on which the trigger fires yields a :class:`Candidate` (taken, or skipped with a
reason) carrying the full point-in-time feature snapshot — the raw material for the
meta-filter and the journal. Strategy-level skips are emitted here; the ``risk``
skip is emitted by the executor after the risk gate runs on a taken candidate.
"""
from __future__ import annotations

import logging

import enum
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from sgt.core.types import Fill, Side
from sgt.intraday.calendar import default_calendar
from sgt.strategy.base import Candidate, Decision, EvalView, PositionView
from sgt.strategy.schema import StrategyConfig
from sgt.strategy.sizing import size_for_decision

CandidateSink = Callable[[Candidate], None]
MetaScorer = Callable[[Mapping[str, float]], float]

_BPS = 1.0e-4


_log = logging.getLogger(__name__)

class State(str, enum.Enum):
    FLAT = "flat"
    ARMED = "armed"
    ENTRY_PENDING = "entry_pending"
    IN_POSITION = "in_position"
    EXIT_PENDING = "exit_pending"


@dataclass(frozen=True, slots=True)
class EntryOverride:
    """Benchmark entry hook. ``take_every`` ignores setup/confirm/filter/meta (and
    regime) and enters on every trigger; ``random`` enters on a trigger with
    probability ``rate`` from a seeded RNG (deterministic per symbol)."""

    mode: str                 # "take_every" | "random"
    rate: float = 1.0
    seed: int = 0


class StrategyRuntime:
    def __init__(
        self,
        config: StrategyConfig,
        symbol: str,
        *,
        candidate_sink: CandidateSink | None = None,
        meta_scorer: MetaScorer | None = None,
        entry_override: EntryOverride | None = None,
        allow_unknown_float: bool = False,
    ) -> None:
        self.config = config
        # Fail closed: a float-capped universe cannot admit a symbol of unknown float
        # unless the caller explicitly opts in (research without reference data).
        self._allow_unknown_float = allow_unknown_float
        self.symbol = symbol
        self._sink = candidate_sink
        self._meta = meta_scorer
        self._override = entry_override
        self.state = State.FLAT
        self.halted = False

        self._armed = False
        self._regime_ok = False
        self._stop_pct = self._find_stop_pct()
        # F-5c entry cutoff: no new entries within this many minutes of the close of the
        # last segment the strategy may trade (post close if extended-hours, else RTH).
        self._cutoff_min = next(
            (c.minutes_before_close for c in config.exits.any_of if c.kind == "session_end"), 5)
        self._rng = random.Random(entry_override.seed) if (
            entry_override is not None and entry_override.mode == "random") else None

        # position state
        self.qty = 0
        self.entry_price = 0.0
        self.entry_ts: datetime | None = None
        self.high_since_entry = 0.0
        self.bars_held = 0
        self._cost_basis = 0.0  # qty*price summed over entry fills (for avg)

    # ------------------------------------------------------------------ helpers
    def _find_stop_pct(self) -> float:
        for c in self.config.exits.any_of:
            if c.kind == "stop_loss_pct":
                return float(c.pct)
        raise ValueError("strategy has no stop_loss_pct exit (grammar validation should prevent this)")

    def position_view(self) -> PositionView | None:
        if self.state not in (State.IN_POSITION, State.EXIT_PENDING) or self.entry_ts is None:
            return None
        return PositionView(
            qty=self.qty, entry_price=self.entry_price, entry_ts=self.entry_ts,
            high_since_entry=self.high_since_entry, bars_held=self.bars_held)

    def _emit(self, ts: datetime, taken: bool, skip_reason: str | None, features: dict[str, float]) -> None:
        if self._sink is not None:
            self._sink(Candidate(ts=ts, symbol=self.symbol, taken=taken,
                                 skip_reason=skip_reason, features=features))

    # ------------------------------------------------------------------ bar close
    def on_bar(self, bar, snap: Mapping[str, float | None], view: EvalView) -> list[Decision]:
        decisions: list[Decision] = []

        # Advance position bookkeeping and inject the live position into the view so
        # exit conditions see the current bar's high/low and holding time.
        if self.state in (State.IN_POSITION, State.EXIT_PENDING):
            self.high_since_entry = max(self.high_since_entry, bar.high)
            self.bars_held += 1
            view = replace(view, position=self.position_view())

        if self.state == State.IN_POSITION:
            self._maybe_exit(bar, view, decisions)

        if self.state in (State.ENTRY_PENDING, State.IN_POSITION, State.EXIT_PENDING):
            # Record a missed candidate when the trigger fires while we are committed.
            if self._trigger_fires(view):
                self._emit(bar.ts, False, "already_in_position", self._features(bar, view, snap))
            return decisions

        # FLAT / ARMED: regime + setup latch, then trigger evaluation.
        self._update_arm(view)
        self._maybe_enter(bar, view, snap, decisions)
        return decisions

    # ------------------------------------------------------------------ exits
    def _maybe_exit(self, bar, view: EvalView, decisions: list[Decision]) -> None:
        for c in self.config.exits.any_of:
            if c.evaluate(view):
                limit = bar.close * (1.0 - self.config.sizing.exit_offset_bps * _BPS)
                decisions.append(Decision(
                    kind="exit", symbol=self.symbol, reason=f"exit:{c.kind}", signal_ts=bar.ts,
                    limit_price=round(limit, 6), qty_hint=self.qty))
                self.state = State.EXIT_PENDING
                return

    # ------------------------------------------------------------------ entries
    def _update_arm(self, view: EvalView) -> None:
        self._regime_ok = all(c.evaluate(view) for c in self.config.regime)
        if not self._regime_ok:
            self.state = State.FLAT
            self._armed = False
            return
        if self.config.setup is None:
            self._armed = True
            self.state = State.ARMED
            return
        if any(c.evaluate(view) for c in self.config.setup.any_of):
            self._armed = True
        self.state = State.ARMED if self._armed else State.FLAT

    def _maybe_enter(self, bar, view: EvalView, snap, decisions: list[Decision]) -> None:
        if not self._trigger_fires(view):
            return
        features = self._features(bar, view, snap)
        skip = self._entry_skip_reason(bar, view, features)
        if skip is not None:
            self._emit(bar.ts, False, skip, features)
            return

        limit = bar.close * (1.0 + self.config.sizing.entry_offset_bps * _BPS)
        stop = limit * (1.0 - self._stop_pct / 100.0)
        score = features.get("meta_score")
        qty = size_for_decision(
            self.config.sizing, limit, stop, score=score,
            size_by_score=bool(self.config.meta_filter and self.config.meta_filter.size_by_score))
        if qty <= 0:
            self._emit(bar.ts, False, "risk", features)  # capital/sizing floor -> cannot size one share
            return

        decisions.append(Decision(
            kind="entry", symbol=self.symbol, reason="entry", signal_ts=bar.ts,
            limit_price=round(limit, 6), stop_price=round(stop, 6), qty_hint=qty, features=features))
        self.state = State.ENTRY_PENDING
        self._armed = False

    def _entry_skip_reason(self, bar, view: EvalView, features: dict[str, float]) -> str | None:
        if self.halted:
            return "halted"
        if self._within_session_cutoff(bar):
            return "session_cutoff"
        universe_skip = self._universe_skip_reason(bar, view)
        if universe_skip is not None:
            return universe_skip
        ov = self._override
        if ov is not None:
            if ov.mode == "take_every":
                return None
            if ov.mode == "random":
                assert self._rng is not None
                return None if self._rng.random() < ov.rate else "override"
        if not self._regime_ok:
            return "regime"
        if self.config.setup is not None and not self._armed:
            return "setup"
        if not self._confirm_passes(view):
            return "confirm"
        if not all(c.evaluate(view) for c in self.config.filters):
            return "filter"
        if self.config.meta_filter is not None:
            score = features.get("meta_score")
            if score is None or score < self.config.meta_filter.threshold:
                return "meta_filter"
        return None

    def _universe_skip_reason(self, bar, view: EvalView) -> str | None:
        """Hard universe gate from ``config.universe`` (2026-10-13, F-8: previously
        never enforced, so a low-float strategy traded any symbol)."""
        u = self.config.universe
        if bar.close < u.price_min or bar.close > u.price_max:
            return "universe:price"
        free_float = view.reference.free_float if view.reference is not None else None
        if free_float is None:
            if not self._allow_unknown_float:
                return "universe:float_unknown"
        elif free_float > u.float_max:
            return "universe:float"
        if not all(c.evaluate(view) for c in self.config.universe_conditions()):
            return "universe:rel_vol"
        return None

    def _confirm_passes(self, view: EvalView) -> bool:
        conf = self.config.confirm
        if conf.k_of_m <= 0:
            return True
        ungrouped = 0
        groups: set[str] = set()
        for c in conf.signals:
            if c.evaluate(view):
                g = c.CORRELATION_GROUP
                if g is None:
                    ungrouped += 1
                else:
                    groups.add(g)
        return (ungrouped + len(groups)) >= conf.k_of_m

    def _trigger_fires(self, view: EvalView) -> bool:
        return self.config.trigger.evaluate(view)

    def _within_session_cutoff(self, bar) -> bool:
        try:
            b = default_calendar().bounds(bar.session_date)
        except ValueError:
            return False
        close = b.post_close if self.config.extended_hours else b.rth_close
        remaining = (close - bar.ts).total_seconds() / 60.0
        # Past the close (e.g. a POST bar for an RTH-only strategy) is also non-entry.
        return remaining <= self._cutoff_min

    # ------------------------------------------------------------------ features
    def _features(self, bar, view: EvalView, snap: Mapping[str, float | None]) -> dict[str, float]:
        feats: dict[str, float] = {k: float(v) for k, v in snap.items() if v is not None}
        feats["minute_of_day"] = float(bar.minute_of_day)
        feats["close"] = float(bar.close)
        feats["bar_volume"] = float(bar.volume)
        prev_close = view.history.closes[-1] if len(view.history) else None
        if prev_close:
            feats["gap_pct"] = (bar.open - prev_close) / prev_close * 100.0
        if bar.vwap:
            feats["dist_vwap_pct"] = (bar.close - bar.vwap) / bar.vwap * 100.0
        if view.session_high_prior:
            feats["dist_hod_pct"] = (bar.close - view.session_high_prior) / view.session_high_prior * 100.0
        if view.reference is not None and view.reference.free_float is not None:
            feats["free_float"] = float(view.reference.free_float)
            feats["dilution_flag"] = 1.0 if view.reference.dilution_flag else 0.0
        if self._meta is not None:
            try:
                feats["meta_score"] = float(self._meta(feats))
            except Exception:  # noqa: BLE001 - a scorer failure must not crash the bar
                # Fail closed (score 0 => meta_filter skip) but never silently.
                _log.exception("meta-filter scorer failed for %s at %s", self.symbol, bar.ts)
                feats["meta_score"] = 0.0
        return feats

    # ------------------------------------------------------------------ restart seeding
    def seed_position(self, qty: int, entry_price: float, entry_ts: datetime) -> None:
        """Adopt a position reconstructed from broker state after a restart: go straight
        to IN_POSITION so exits are evaluated on the next bar and no new entry is taken
        while the position is held. A later SELL fill resets to FLAT via ``on_fill``."""
        if qty <= 0:
            return
        self.qty = int(qty)
        self.entry_price = float(entry_price)
        self.entry_ts = entry_ts
        self._cost_basis = float(entry_price) * int(qty)
        self.high_since_entry = float(entry_price)
        self.bars_held = 0
        self._armed = False
        self.state = State.IN_POSITION

    # ------------------------------------------------------------------ executor callbacks
    def reset_entry(self) -> None:
        """Roll back an ENTRY_PENDING to FLAT (risk rejected, timed out, or unplaceable)."""
        if self.state == State.ENTRY_PENDING:
            self.state = State.FLAT
            self._armed = False

    def enter_exit_pending(self) -> None:
        """The order lifecycle has an exit order in flight (a forced/retried exit the
        runtime did not itself emit); mark the FSM EXIT_PENDING so it stops proposing
        its own exits for this position. A fill brings it to FLAT via ``on_fill``."""
        if self.state in (State.IN_POSITION, State.EXIT_PENDING):
            self.state = State.EXIT_PENDING

    def on_halt(self, halted: bool) -> None:
        self.halted = halted

    def on_fill(self, fill: Fill) -> None:
        if fill.symbol != self.symbol:
            return
        if fill.side is Side.BUY:
            self.qty += fill.qty
            self._cost_basis += fill.qty * fill.price
            self.entry_price = self._cost_basis / self.qty if self.qty else fill.price
            if self.entry_ts is None:
                self.entry_ts = fill.ts
                self.high_since_entry = fill.price
                self.bars_held = 0
            self.high_since_entry = max(self.high_since_entry, fill.price)
            self.state = State.IN_POSITION
        else:  # SELL (discretionary exit or protective stop)
            self.qty -= fill.qty
            if self.qty <= 0:
                self._reset_position()

    def _reset_position(self) -> None:
        self.qty = 0
        self.entry_price = 0.0
        self.entry_ts = None
        self.high_since_entry = 0.0
        self.bars_held = 0
        self._cost_basis = 0.0
        self.state = State.FLAT
        self._armed = False
