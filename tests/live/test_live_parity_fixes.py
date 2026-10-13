"""Money-path parity fixes (E-1/E-2/L-A/L-E): live zero-allocation no longer freezes a
symbol; a daily realized+unrealized loss breach flattens held positions once, identically
in backtest and shadow; a stale feed with open positions alerts once and re-places a
missing broker-side stop."""
from __future__ import annotations

from datetime import date

from live_fakes import CFG, RecordingNotifier, build_executor, dataset

from sgt.accounts.registry import AccountSpec
from sgt.brokers.alert_only import LogNotifier
from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.brokers.sim import SimBroker
from sgt.core.types import Fill, Mode, Segment, Side
from sgt.engine.backtest import default_risk_config, run_backtest
from sgt.engine.engine import StrategyEngine
from sgt.engine.eventlog import InMemoryEventSink
from sgt.live.alerts import AlertManager
from sgt.live.executor import LiveExecutor
from sgt.live.replay import assert_replay_parity, replay_session
from sgt.risk.exposure import DictExposureView
from sgt.risk.limits import LadderStage
from sgt.risk.manager import RiskManager
from sgt.risk.state import InMemoryRiskStore
from sgt.strategy.base import Candidate, Decision
from sgt.strategy.loader import config_hash as compute_config_hash
from sgt.strategy.runtime import EntryOverride, State


# --------------------------------------------------------------------------- E-2

def _zero_alloc_executor():
    sink = InMemoryEventSink()
    candidates: list[Candidate] = []
    engine = StrategyEngine(CFG, entry_override=EntryOverride("take_every"),
                            candidate_sink=candidates.append, allow_unknown_float=True)
    adapter = SimBroker("acct", DEFAULT_COST_MODEL, 100_000.0)
    exposure = DictExposureView()
    store = InMemoryRiskStore(namespace="shadow-zeroalloc")
    risk = RiskManager(default_risk_config(CFG), exposure, store=store, mode=Mode.SHADOW)
    risk.arm(actor="t", reason="t")
    store.upsert_strategy(CFG.id, enabled=True, stage=LadderStage.STANDARD)
    # max_notional of $0.50 is below one share, so every entry allocates zero legs.
    spec = AccountSpec("acct", "sim", "cash", True, 1.0, 0.5)
    ex = LiveExecutor(CFG, engine, risk=risk, adapters={"acct": adapter}, exposure=exposure,
                      cost_model=DEFAULT_COST_MODEL, event_sink=sink, candidate_sink=candidates.append,
                      alerts=AlertManager(LogNotifier()), run_id="zeroalloc",
                      config_hash=compute_config_hash(CFG), mode=Mode.SHADOW, accounts=[spec])
    return ex, sink, candidates, engine


async def test_live_zero_allocation_entry_resets_runtime_and_does_not_freeze():
    ex, sink, candidates, engine = _zero_alloc_executor()
    data = dataset(["AAAA"])
    for b in data["AAAA"]:
        await ex.process_bar(b)
    buys = [e for e in sink.events if e["kind"] == "order" and e["payload"]["side"] == "buy"]
    assert buys == [], "a zero-allocation entry must place no order"
    alloc_skips = [c for c in candidates if not c.taken and c.skip_reason == "allocation"]
    assert len(alloc_skips) >= 2, "repeated entries must keep being proposed (symbol not frozen)"
    # The symbol is not stuck in ENTRY_PENDING at the end of the run.
    rt = engine.runtime_for("AAAA")
    assert rt is not None and rt.state is not State.ENTRY_PENDING


# --------------------------------------------------------------------------- L-A risk_flatten

def _flatten_risk_config():
    rc = default_risk_config(CFG)
    # A $1 daily global loss limit: any unrealized loss on the held position breaches it.
    return rc.model_copy(update={"limits": rc.limits.model_copy(
        update={"daily_loss_limit_global": 1.0, "daily_loss_limit_per_account": 1.0})})


async def test_risk_flatten_closes_positions_once_backtest_and_shadow_parity():
    data = dataset(["AAAA"])
    rc = _flatten_risk_config()
    bt_sink = InMemoryEventSink()
    bt = run_backtest(CFG, data, starting_cash=100_000.0, event_sink=bt_sink, run_id="bt",
                      entry_override=EntryOverride("take_every"), risk_config=rc, allow_unknown_float=True)
    sh = await replay_session(CFG, data, starting_cash=100_000.0, run_id="sh",
                              entry_override=EntryOverride("take_every"), risk_config=rc,
                              allow_unknown_float=True)
    assert_replay_parity(bt_sink, sh.events)
    bt_flat = [e for e in bt_sink.events if e["kind"] == "risk_flatten"]
    sh_flat = [e for e in sh.events.events if e["kind"] == "risk_flatten"]
    # One summary per session in which a flatten occurs (PASS 3 F-2), identical in both runs.
    assert len(bt_flat) >= 1 and len(bt_flat) == len(sh_flat)
    bt_days = [e["ts"].date() for e in bt_flat]
    assert len(bt_days) == len(set(bt_days)), "at most one risk_flatten summary per session"
    assert "global" in bt_flat[0]["payload"]["scopes"]
    # The flattened position closed with the risk_flatten exit reason, in both runs.
    assert (bt.trades["exit_reason"] == "risk_flatten").any()
    assert any(t["exit_reason"] == "risk_flatten" for t in sh.trades)


# --------------------------------------------------------------------------- L-E on_feed_stale

async def test_on_feed_stale_alerts_once_and_replaces_missing_stop():
    notifier = RecordingNotifier()
    h = build_executor(arm=True, entry_override=EntryOverride("take_every"), notifier=notifier)
    lc = h.executor._lc("acct")
    bar = dataset(["AAAA"])["AAAA"][0]  # an RTH bar (segment supports broker stops)
    assert bar.segment is Segment.RTH
    # Open a position with a working protective stop in the lifecycle + exposure view.
    cid = next(a.intent.client_order_id for a in lc.on_decision(
        Decision(kind="entry", symbol="AAAA", reason="entry", signal_ts=bar.ts,
                 limit_price=round(bar.close + 0.01, 6), stop_price=round(bar.close * 0.97, 6),
                 qty_hint=100, features={}), bar) if hasattr(a, "intent"))
    lc.on_fill(Fill(cid, "x", "acct", "AAAA", Side.BUY, 100, bar.close, bar.ts, "f0"), bar)
    h.executor._last_bar["AAAA"] = bar
    h.exposure.set_position("acct", "AAAA", 100, mark=bar.close)
    assert lc._state["AAAA"].stop is not None and lc._state["AAAA"].stop.working
    # Simulate the broker-side stop going missing, then a stale feed while holding.
    lc._state["AAAA"].stop.terminal = True
    await h.executor.on_feed_stale(bar.ts)
    await h.executor.on_feed_stale(bar.ts)  # a second stall tick must not re-alert
    title = "CRITICAL: FEED STALE WITH OPEN POSITIONS"
    assert sum(1 for t, _b in notifier.sent if t == title) == 1
    replaced = [e for e in h.sink.events
                if e["kind"] == "order" and e["payload"]["order_type"] == "stop"]
    assert replaced, "a missing protective stop must be re-placed on a stale feed"
    assert lc._state["AAAA"].stop is not None and lc._state["AAAA"].stop.working


def test_risk_flatten_summary_journaled_every_session_it_occurs():
    """PASS 3 F-2: the risk_flatten audit summary must not be suppressed after day 1."""
    from sgt.intraday.calendar import default_calendar

    data = dataset(["AAAA"])
    sink = InMemoryEventSink()
    res = run_backtest(CFG, data, starting_cash=100_000.0, event_sink=sink, run_id="bt-multi",
                       entry_override=EntryOverride("take_every"), risk_config=_flatten_risk_config(),
                       allow_unknown_float=True)
    cal = default_calendar()
    flat_trades = res.trades[res.trades["exit_reason"] == "risk_flatten"]
    trade_sessions = {cal.session_of(ts.to_pydatetime()) for ts in flat_trades["exit_ts"]}
    event_sessions = {cal.session_of(e["ts"]) for e in sink.events if e["kind"] == "risk_flatten"}
    assert trade_sessions, "fixture must produce at least one risk_flatten exit"
    assert trade_sessions <= event_sessions, (trade_sessions, event_sessions)
