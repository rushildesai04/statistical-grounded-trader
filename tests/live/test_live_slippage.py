"""Slippage feed (G-3): the executor records realized-vs-modeled slippage per trade, and
realized >> modeled over the monitor window HALTs the strategy and disarms the namespace."""
from __future__ import annotations

from datetime import datetime, timezone

from live_fakes import CFG, build_executor, dataset

from sgt.accounts.registry import AccountSpec
from sgt.core.types import Mode
from sgt.db.session import get_session
from sgt.engine.backtest import default_risk_config
from sgt.live.replay import InMemoryLiveSource
from sgt.live.runner import LiveRunner
from sgt.risk.state import RiskStore
from sgt.search import registry
from sgt.strategy.loader import config_hash as compute_config_hash
from sgt.strategy.runtime import EntryOverride

_SPEC = AccountSpec("acct", "sim", "cash", True, 1.0, 0.0)
_HASH = compute_config_hash(CFG)
_TS = datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)


async def _noop_sleep(_s: float) -> None:
    return None


async def test_executor_records_realized_and_modeled_slippage():
    rows: list[dict] = []
    h = build_executor(arm=True, entry_override=EntryOverride("take_every"),
                       on_closed_trade=rows.append)
    last = await h.drive(dataset(["AAAA"]))
    h.executor.close_open_positions(last)
    assert rows, "expected at least one closed trade"
    for r in rows:
        assert "modeled_slippage" in r and "realized_slippage" in r
        assert r["realized_slippage"] >= 0.0 and r["modeled_slippage"] >= 0.0
    assert any(r["modeled_slippage"] > 0.0 for r in rows), "modeled slippage must be nonzero"


async def test_realized_slippage_drift_halts_and_disarms():
    with get_session() as s:
        registry.upsert_registry(s, config_hash=_HASH, config={}, status="validated",
                                 evidence={"decay_baseline": {
                                     "expectancy": 0.01, "ret_std": 0.02, "win_rate": 0.55}})
    from sgt.brokers.costs import DEFAULT_COST_MODEL
    from sgt.brokers.sim import SimBroker
    r = LiveRunner("tests/fixtures/trader_v0_mechanics.yaml", Mode.PAPER, [_SPEC],
                   source=InMemoryLiveSource([], delay_minutes=0, consolidated=True),
                   adapters={"acct": SimBroker("acct", DEFAULT_COST_MODEL, 100_000.0)},
                   risk_config=default_risk_config(CFG), clock=lambda: _TS,
                   sleep=_noop_sleep, run_id="paper-test")
    await r.startup()
    r.risk.arm(actor="test", reason="test")
    assert RiskStore("paper").get_switch().armed
    for _ in range(20):
        r._feed_decay({"strategy_id": CFG.id, "ret": 0.0, "pnl": 0.0, "exit_ts": _TS,
                       "realized_slippage": 5.0, "modeled_slippage": 1.0})
    actions = [e["payload"]["action"] for e in r.sink.events if e["kind"] == "decay_action"]
    assert "halt" in actions, f"realized >> modeled must HALT, saw {actions}"
    assert not RiskStore("paper").get_switch().armed, "HALT must disarm the namespace"
