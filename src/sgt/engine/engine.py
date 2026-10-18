"""StrategyEngine: the single per-bar decision path shared by backtest, shadow,
paper and live (plan §3.1). It owns, per symbol, an :class:`IndicatorBank`, the
bounded history ring buffers conditions read, session high-of-day trackers,
point-in-time reference data, and a :class:`StrategyRuntime`.

The engine is pure and synchronous: ``on_bar`` consumes one completed bar and
returns the runtime's :class:`Decision`s; it NEVER talks to a broker or the risk
layer (the executor does). ``on_fill`` / ``on_halt`` route broker feedback to the
owning symbol's runtime.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime

from sgt.core.interfaces import IndicatorSpec, ReferenceData
from sgt.core.types import Bar, Fill, HaltEvent, HaltState, Segment
from sgt.indicators.registry import IndicatorBank, IndicatorContext
from sgt.intraday.calendar import SessionCalendar, default_calendar
from sgt.strategy.base import Candidate, Decision, EvalView, HistoryView
from sgt.strategy.runtime import EntryOverride, MetaScorer, StrategyRuntime
from sgt.strategy.schema import ModelStrategyConfig, StrategyConfig

ReferenceFn = Callable[[str, date], ReferenceData | None]


def _normalize_reference(reference: object) -> ReferenceFn | None:
    if reference is None:
        return None
    if callable(reference):
        return reference  # type: ignore[return-value]
    if hasattr(reference, "get") and not isinstance(reference, Mapping):
        return lambda sym, d: reference.get(sym, d)  # provider with .get(symbol, as_of)
    if isinstance(reference, Mapping):
        return lambda sym, d: reference.get(sym)  # static symbol -> ReferenceData
    raise TypeError(f"unsupported reference source: {type(reference).__name__}")


@dataclass(slots=True)
class _SymCtx:
    bank: IndicatorBank
    runtime: object  # StrategyRuntime | sgt.decision.runtime.DecisionRuntime
    reference: ReferenceData | None
    session_date: date | None
    session_high: float | None   # max high over PRE+RTH bars seen so far this session
    premarket_high: float | None
    prev_snap: dict[str, float | None] | None
    closes: deque
    highs: deque
    lows: deque
    volumes: deque
    snaps: deque


class StrategyEngine:
    def __init__(
        self,
        config: StrategyConfig,
        *,
        calendar: SessionCalendar | None = None,
        reference: object = None,
        meta_scorer: MetaScorer | None = None,
        entry_override: EntryOverride | None = None,
        candidate_sink: Callable[[Candidate], None] | None = None,
        allow_unknown_float: bool = False,
        policy: object = None,
        cost_source: object = None,
    ) -> None:
        self.config = config
        self._is_model = isinstance(config, ModelStrategyConfig)
        self._allow_unknown_float = allow_unknown_float
        self.calendar = calendar or default_calendar()
        self._reference_fn = _normalize_reference(reference)
        self._meta = meta_scorer
        self._override = entry_override
        self._candidate_sink = candidate_sink
        self._policy = policy
        self._cost_source = cost_source
        if self._is_model:
            if policy is None:
                raise ValueError("a model strategy requires an injected policy")
            from sgt.decision.runtime import model_history_window, model_indicator_specs
            self._specs = model_indicator_specs(config)
            self._history = model_history_window(config)
        else:
            self._specs = self._collect_specs(config)
            self._history = max((c.history_window() for c in config.all_conditions()), default=0)
        self._maxlen = max(self._history, 1)
        self._ctx: dict[str, _SymCtx] = {}

    @staticmethod
    def _collect_specs(config: StrategyConfig) -> list[IndicatorSpec]:
        seen: dict[str, IndicatorSpec] = {}
        for c in config.all_conditions():
            for spec in c.required_specs():
                seen.setdefault(spec.key, spec)
        return list(seen.values())

    def runtime_for(self, symbol: str):
        ctx = self._ctx.get(symbol)
        return ctx.runtime if ctx else None

    def _open_risk_usd(self) -> float:
        """Total dollar at-risk across every model runtime's open/pending position.

        Feeds each :class:`~sgt.decision.runtime.DecisionRuntime`'s portfolio at-risk cap.
        The engine is per strategy, so summing over its runtimes is the whole strategy's
        exposure."""
        total = 0.0
        for ctx in self._ctx.values():
            fn = getattr(ctx.runtime, "at_risk_usd", None)
            if fn is not None:
                total += fn()
        return total

    # ------------------------------------------------------------------ per-symbol context
    def _ensure_ctx(self, symbol: str, session_date: date) -> _SymCtx:
        ctx = self._ctx.get(symbol)
        if ctx is None:
            ref = self._reference_fn(symbol, session_date) if self._reference_fn else None
            bank = IndicatorBank(self._specs, IndicatorContext(reference=ref))
            if self._is_model:
                from sgt.decision.runtime import DecisionRuntime
                runtime = DecisionRuntime(
                    self.config, symbol, policy=self._policy, cost_source=self._cost_source,
                    candidate_sink=self._candidate_sink,
                    allow_unknown_float=self._allow_unknown_float,
                    open_risk_fn=self._open_risk_usd)
            else:
                runtime = StrategyRuntime(
                    self.config, symbol, candidate_sink=self._candidate_sink,
                    meta_scorer=self._meta, entry_override=self._override,
                    allow_unknown_float=self._allow_unknown_float)
            ctx = _SymCtx(
                bank=bank, runtime=runtime, reference=ref, session_date=session_date,
                session_high=None, premarket_high=None, prev_snap=None,
                closes=deque(maxlen=self._maxlen), highs=deque(maxlen=self._maxlen),
                lows=deque(maxlen=self._maxlen), volumes=deque(maxlen=self._maxlen),
                snaps=deque(maxlen=self._maxlen))
            self._ctx[symbol] = ctx
        elif ctx.session_date != session_date:
            # New session: refresh point-in-time reference and reset intraday HOD trackers.
            ctx.reference = self._reference_fn(symbol, session_date) if self._reference_fn else None
            ctx.session_date = session_date
            ctx.session_high = None
            ctx.premarket_high = None
        return ctx

    # ------------------------------------------------------------------ bar / fill / halt
    def on_bar(self, bar: Bar) -> list[Decision]:
        ctx = self._ensure_ctx(bar.symbol, bar.session_date)
        ctx.bank.update(bar)
        snap = ctx.bank.snapshot()
        windows = self.calendar.windows(bar.ts)
        view = EvalView(
            bar=bar, snap_now=snap, snap_prev=ctx.prev_snap, windows=windows,
            history=HistoryView(
                closes=list(ctx.closes), highs=list(ctx.highs), lows=list(ctx.lows),
                volumes=list(ctx.volumes), snaps=list(ctx.snaps)),
            position=None, reference=ctx.reference,
            session_high_prior=ctx.session_high, premarket_high=ctx.premarket_high)

        decisions = ctx.runtime.on_bar(bar, snap, view)

        # Advance history and session trackers AFTER the decision (point-in-time).
        ctx.closes.append(bar.close)
        ctx.highs.append(bar.high)
        ctx.lows.append(bar.low)
        ctx.volumes.append(bar.volume)
        ctx.snaps.append(snap)
        ctx.prev_snap = snap
        if bar.segment in (Segment.PRE, Segment.RTH):
            ctx.session_high = bar.high if ctx.session_high is None else max(ctx.session_high, bar.high)
        if bar.segment is Segment.PRE:
            ctx.premarket_high = bar.high if ctx.premarket_high is None else max(ctx.premarket_high, bar.high)
        return decisions

    def seed_position(self, symbol: str, qty: int, entry_price: float, entry_ts: datetime,
                      session_date: date) -> None:
        """Seed a reconstructed position into ``symbol``'s runtime after a restart so the
        shared decision path treats it as held (exits evaluated, no re-entry). Called by
        the live runner during startup reconciliation; never used in backtest."""
        ctx = self._ensure_ctx(symbol, session_date)
        ctx.runtime.seed_position(qty, entry_price, entry_ts)

    def on_fill(self, fill: Fill) -> None:
        ctx = self._ctx.get(fill.symbol)
        if ctx is not None:
            ctx.runtime.on_fill(fill)

    def on_halt(self, event: HaltEvent) -> None:
        ctx = self._ctx.get(event.symbol)
        if ctx is not None:
            ctx.runtime.on_halt(event.state is HaltState.HALTED)
