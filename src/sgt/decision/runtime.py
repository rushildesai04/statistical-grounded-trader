"""Model-driven per-symbol runtime (docs/DECISION_MODEL.md §7, §9).

:class:`DecisionRuntime` is the ``kind: model`` counterpart to
:class:`sgt.strategy.runtime.StrategyRuntime`. It runs the same FLAT -> ENTRY_PENDING
-> IN_POSITION -> EXIT_PENDING FSM and emits the same :class:`Decision` objects, so the
engine, order lifecycle, risk gate, brokers and live runner are unchanged. The
difference is the entry logic: on a trigger (while flat) with no hard veto it builds
the point-in-time feature vector and asks an injected :class:`ModelPolicy` whether to
trade and how large; exits are the ATR take-profit / ATR stop / time stop / session-end
bracket. Every candidate (taken or skipped) is journaled with its features and, when
the policy ran, its mu / sigma / p and decision reason.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from sgt.core.interfaces import IndicatorSpec, ReferenceData
from sgt.core.types import Bar, Fill, Side
from sgt.decision.features import (
    FEATURE_HISTORY,
    SPEC_ATR,
    CostModelAdapter,
    CostSource,
    FeatureContext,
    compute_features,
    feature_indicator_specs,
)
# PolicyDecision / ModelPolicy are owned by the trainer workstream (single source of
# truth); the DecisionRuntime imports them so field names never drift.
from sgt.decision.policy import (
    ModelPolicy,
    PolicyDecision,
    PolicyParams,
    validate_exit_consistency,
)
from sgt.decision.triggers import fired_trigger, trigger_indicator_specs
from sgt.strategy.base import Candidate, Decision, EvalView, PositionView
from sgt.strategy.exits import AtrStop
from sgt.strategy.runtime import State
from sgt.strategy.schema import ModelStrategyConfig
from sgt.intraday.calendar import default_calendar

_BPS = 1.0e-4
_log = logging.getLogger(__name__)

CandidateSink = Callable[[Candidate], None]
OpenRiskFn = Callable[[], float]


# --------------------------------------------------------------------------- policy context

@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Point-in-time facts the policy reads beyond the feature vector.

    ``open_risk`` is the fraction of allocation already at risk across ALL of the
    strategy's open positions (not just this symbol) so the policy's portfolio at-risk
    cap engages; the engine injects it via ``open_risk_fn``. ``mark_price`` /
    ``round_trip_cost`` are the canonical names the policy reads (``close`` / ``cost_frac``
    remain as aliases).
    """

    symbol: str
    ts: datetime
    mark_price: float
    atr: float | None
    allocation_usd: float
    max_size_fraction: float
    round_trip_cost: float | None
    open_risk: float
    reference: ReferenceData | None

    @property
    def close(self) -> float:
        return self.mark_price

    @property
    def cost_frac(self) -> float | None:
        return self.round_trip_cost


# --------------------------------------------------------------------------- spec helpers

def model_indicator_specs(config: ModelStrategyConfig) -> list[IndicatorSpec]:
    """Every indicator the model path needs: features + triggers + exit bracket ATR +
    the regime/universe veto conditions."""
    seen: dict[str, IndicatorSpec] = {}
    for spec in (*feature_indicator_specs(), *trigger_indicator_specs(), SPEC_ATR):
        seen.setdefault(spec.key, spec)
    seen.setdefault(IndicatorSpec.of("atr", n=config.bracket.atr_n).key,
                    IndicatorSpec.of("atr", n=config.bracket.atr_n))
    for c in config.all_conditions():
        for spec in c.required_specs():
            seen.setdefault(spec.key, spec)
    for c in config.exits.any_of:
        for spec in c.required_specs():
            seen.setdefault(spec.key, spec)
    return list(seen.values())


def model_history_window(config: ModelStrategyConfig) -> int:
    reg = max((c.history_window() for c in config.all_conditions()), default=0)
    return max(FEATURE_HISTORY, reg)


# --------------------------------------------------------------------------- runtime

class DecisionRuntime:
    def __init__(
        self,
        config: ModelStrategyConfig,
        symbol: str,
        *,
        policy: ModelPolicy,
        cost_source: CostSource | None = None,
        candidate_sink: CandidateSink | None = None,
        allow_unknown_float: bool = False,
        open_risk_fn: OpenRiskFn | None = None,
    ) -> None:
        self.config = config
        self.symbol = symbol
        self._policy = policy
        self._cost = cost_source if cost_source is not None else CostModelAdapter()
        self._sink = candidate_sink
        self._allow_unknown_float = allow_unknown_float
        self._open_risk_fn = open_risk_fn
        self._enabled = config.triggers_enabled
        # Evaluate the ATR stop before the ATR take-profit so a bar that touches BOTH
        # barriers resolves to the STOP, matching the training label's same-bar
        # double-touch rule (sgt.decision.labels; DECISION_MODEL.md §5 F1). The schema
        # emits (take_profit, stop, time, session_end); a stable sort that ranks AtrStop
        # first preserves every other exit's relative order.
        self._exits = tuple(sorted(config.exits.any_of,
                                   key=lambda c: 0 if isinstance(c, AtrStop) else 1))
        self._cutoff_min = config.bracket.session_end_minutes_before_close

        # F4 (DECISION_MODEL.md §10): the runtime exit bracket, the policy's k values and
        # the label config the model was trained on must agree, or every bar would resolve
        # against barriers the model never optimized. Enforce it once at construction when
        # the policy exposes its params + model metadata (the real EnsembleModelPolicy).
        pp = getattr(policy, "params", None)
        meta = getattr(policy, "model_metadata", None)
        if isinstance(pp, PolicyParams) and isinstance(meta, Mapping):
            validate_exit_consistency(config.bracket, pp, meta)

        self.state = State.FLAT
        self.halted = False

        # ATR frozen at the signal bar used to size the entry; defines the fixed exit
        # barriers for the open position (DECISION_MODEL.md §5 F1).
        self._entry_atr: float | None = None

        # protective-stop bookkeeping for the portfolio at-risk aggregation.
        self._stop_price = 0.0
        self._entry_limit = 0.0
        self._planned_qty = 0

        # session-high tracking for the bars_since_session_high feature (O(1), causal).
        self._session_date = None
        self._session_high: float | None = None
        self._bars_since_high: int | None = None

        # position state
        self.qty = 0
        self.entry_price = 0.0
        self.entry_ts: datetime | None = None
        self.high_since_entry = 0.0
        self.bars_held = 0
        self._cost_basis = 0.0

    # ------------------------------------------------------------------ helpers
    def position_view(self) -> PositionView | None:
        if self.state not in (State.IN_POSITION, State.EXIT_PENDING) or self.entry_ts is None:
            return None
        return PositionView(qty=self.qty, entry_price=self.entry_price, entry_ts=self.entry_ts,
                            high_since_entry=self.high_since_entry, bars_held=self.bars_held,
                            entry_atr=self._entry_atr)

    def at_risk_usd(self) -> float:
        """Dollar risk of this symbol's open/pending position = (entry - stop) * qty.

        Summed across a strategy's runtimes by the engine to feed the policy's portfolio
        at-risk cap. A filled position uses its average entry; a still-pending entry uses
        its limit and planned qty so the cap also bounds concurrent same-bar entries."""
        if self.qty > 0 and self._stop_price > 0.0:
            return max(0.0, self.entry_price - self._stop_price) * self.qty
        if self.state is State.ENTRY_PENDING and self._planned_qty > 0 and self._stop_price > 0.0:
            return max(0.0, self._entry_limit - self._stop_price) * self._planned_qty
        return 0.0

    def _emit(self, ts, taken, skip_reason, features) -> None:
        if self._sink is not None:
            self._sink(Candidate(ts=ts, symbol=self.symbol, taken=taken,
                                 skip_reason=skip_reason, features=features))

    def _track_session_high(self, bar: Bar) -> None:
        if bar.session_date != self._session_date:
            self._session_date = bar.session_date
            self._session_high = bar.high
            self._bars_since_high = 0
            return
        if self._session_high is None or bar.high >= self._session_high:
            self._session_high = bar.high
            self._bars_since_high = 0
        else:
            self._bars_since_high = (self._bars_since_high or 0) + 1

    # ------------------------------------------------------------------ bar close
    def on_bar(self, bar: Bar, snap: Mapping[str, float | None], view: EvalView) -> list[Decision]:
        self._track_session_high(bar)
        decisions: list[Decision] = []

        if self.state in (State.IN_POSITION, State.EXIT_PENDING):
            self.high_since_entry = max(self.high_since_entry, bar.high)
            self.bars_held += 1
            view = replace(view, position=self.position_view())

        if self.state == State.IN_POSITION:
            self._maybe_exit(bar, view, decisions)

        if self.state in (State.ENTRY_PENDING, State.IN_POSITION, State.EXIT_PENDING):
            if fired_trigger(view, self._enabled) is not None:
                self._emit(bar.ts, False, "already_in_position", self._base_features(bar, view, snap))
            return decisions

        self._maybe_enter(bar, view, snap, decisions)
        return decisions

    # ------------------------------------------------------------------ exits
    def _maybe_exit(self, bar: Bar, view: EvalView, decisions: list[Decision]) -> None:
        for c in self._exits:
            if c.evaluate(view):
                limit = bar.close * (1.0 - self.config.sizing.exit_offset_bps * _BPS)
                decisions.append(Decision(
                    kind="exit", symbol=self.symbol, reason=f"exit:{c.kind}", signal_ts=bar.ts,
                    limit_price=round(limit, 6), qty_hint=self.qty))
                self.state = State.EXIT_PENDING
                return

    # ------------------------------------------------------------------ entries
    def _maybe_enter(self, bar: Bar, view: EvalView, snap, decisions: list[Decision]) -> None:
        trig = fired_trigger(view, self._enabled)
        if trig is None:
            return
        features = self._features(bar, view, snap, trig)
        veto = self._veto_reason(bar, view)
        if veto is not None:
            self._emit(bar.ts, False, veto, features)
            return

        atr = snap.get(SPEC_ATR.key + ".value")
        atr = atr if (atr is not None and atr > 0.0) else None
        cost_frac = features.get("f_cost_rt_pct")
        cost_frac = cost_frac / 100.0 if cost_frac == cost_frac else None  # NaN check
        alloc = self.config.sizing.allocation_usd
        open_risk = (self._open_risk_fn() / alloc) if (self._open_risk_fn and alloc > 0) else 0.0
        ctx = PolicyContext(
            symbol=self.symbol, ts=bar.ts, mark_price=bar.close, atr=atr,
            allocation_usd=alloc, max_size_fraction=self.config.model.max_size_fraction,
            round_trip_cost=cost_frac, open_risk=open_risk, reference=view.reference)
        try:
            pd = self._policy.decide(features, ctx)
        except Exception:  # noqa: BLE001 - a policy failure must not crash the bar
            _log.exception("policy failed for %s at %s", self.symbol, bar.ts)
            self._emit(bar.ts, False, "policy_error", features)
            return

        journal = self._with_policy(features, pd)
        if not pd.trade:
            self._emit(bar.ts, False, pd.reason or "model", journal)
            return

        entry = bar.close * (1.0 + self.config.sizing.entry_offset_bps * _BPS)
        stop = pd.stop_price
        if stop is None and atr is not None:
            stop = entry - self.config.bracket.k_sl * atr
        qty = self._size(entry, stop, pd.size_fraction)
        if qty <= 0:
            self._emit(bar.ts, False, "risk", journal)
            return

        self._stop_price = float(stop)
        self._entry_limit = float(entry)
        self._planned_qty = qty
        # Freeze the signal-bar ATR that placed the brackets so the exit barriers match
        # the training labels (DECISION_MODEL.md §5 F1). ``atr`` is the same value the
        # stop above and the policy's bracket prices were computed from.
        self._entry_atr = atr
        decisions.append(Decision(
            kind="entry", symbol=self.symbol, reason="entry", signal_ts=bar.ts,
            limit_price=round(entry, 6), stop_price=round(float(stop), 6), qty_hint=qty,
            features=journal))
        self.state = State.ENTRY_PENDING

    def _size(self, entry: float, stop: float | None, size_fraction: float) -> int:
        import math

        if stop is None or entry <= 0 or stop <= 0 or entry <= stop:
            return 0
        frac = max(0.0, min(size_fraction, self.config.model.max_size_fraction))
        if frac <= 0.0:
            return 0
        risk_budget = self.config.sizing.allocation_usd * frac
        qty = math.floor(risk_budget / (entry - stop))
        qty = min(qty, math.floor(self.config.sizing.max_notional_usd / entry))
        return qty if qty >= 1 else 0

    def _veto_reason(self, bar: Bar, view: EvalView) -> str | None:
        if self.halted:
            return "halted"
        if self._within_session_cutoff(bar):
            return "session_cutoff"
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
        if not all(c.evaluate(view) for c in self.config.regime):
            return "regime"
        return None

    def _within_session_cutoff(self, bar: Bar) -> bool:
        try:
            b = default_calendar().bounds(bar.session_date)
        except ValueError:
            return False
        close = b.post_close if self.config.extended_hours else b.rth_close
        return (close - bar.ts).total_seconds() / 60.0 <= self._cutoff_min

    # ------------------------------------------------------------------ features
    def _features(self, bar: Bar, view: EvalView, snap, trig: str) -> dict[str, float]:
        try:
            cost_frac, _src = self._cost.round_trip(self.symbol, bar.ts, bar.close)
        except Exception:  # noqa: BLE001 - cost lookup failure degrades to NaN, logged
            _log.exception("cost lookup failed for %s at %s", self.symbol, bar.ts)
            cost_frac = None
        fctx = FeatureContext(
            bar=bar, snap=snap, snap_prev=view.snap_prev, history=view.history,
            reference=view.reference, cost_frac=cost_frac,
            session_high_prior=view.session_high_prior, premarket_high=view.premarket_high,
            bars_since_session_high=self._bars_since_high, trigger=trig, windows=view.windows)
        return compute_features(fctx)

    def _base_features(self, bar: Bar, view: EvalView, snap) -> dict[str, float]:
        return self._features(bar, view, snap, "")

    @staticmethod
    def _with_policy(features: dict[str, float], pd: PolicyDecision) -> dict[str, float]:
        out = dict(features)
        out["policy_size_fraction"] = float(pd.size_fraction)
        for k, v in dict(pd.contributions).items():
            out[f"policy_{k}"] = float(v)
        return out

    # ------------------------------------------------------------------ restart seeding
    def seed_position(self, qty: int, entry_price: float, entry_ts: datetime) -> None:
        if qty <= 0:
            return
        self.qty = int(qty)
        self.entry_price = float(entry_price)
        self.entry_ts = entry_ts
        self._cost_basis = float(entry_price) * int(qty)
        self.high_since_entry = float(entry_price)
        self.bars_held = 0
        self.state = State.IN_POSITION

    # ------------------------------------------------------------------ executor callbacks
    def reset_entry(self) -> None:
        if self.state == State.ENTRY_PENDING:
            self.state = State.FLAT
            self._planned_qty = 0
            if self.qty <= 0:
                self._stop_price = 0.0
                self._entry_limit = 0.0
                self._entry_atr = None

    def enter_exit_pending(self) -> None:
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
        else:
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
        self._stop_price = 0.0
        self._entry_limit = 0.0
        self._planned_qty = 0
        self._entry_atr = None
        self.state = State.FLAT
