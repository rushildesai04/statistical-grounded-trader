"""Decay baselines (G-2): LIVE fails closed without a validated baseline; a registry
baseline (or a backtest-derived one) builds the full supervisor; a planted losing streak
in paper applies and audits a DEMOTE."""
from __future__ import annotations

from datetime import datetime, timezone

from live_fakes import CFG, dataset

from sgt.accounts.registry import AccountSpec
from sgt.brokers.sim import SimBroker
from sgt.core.types import Mode
from sgt.db.session import get_session
from sgt.engine.backtest import default_risk_config
from sgt.live.decay_wiring import baseline_from_backtest
from sgt.live.replay import InMemoryLiveSource
from sgt.live.runner import BaselineMissingError, LiveRunner
from sgt.risk.state import RiskStore
from sgt.search import registry
from sgt.strategy.loader import config_hash as compute_config_hash

_SPEC = AccountSpec("acct", "sim", "cash", True, 1.0, 0.0)
_HASH = compute_config_hash(CFG)
_TS = datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)


async def _noop_sleep(_s: float) -> None:
    return None


class _LiveSim(SimBroker):
    """SimBroker standing in for a live-capable adapter (is_paper=False) so LIVE-mode
    runners pass the startup adapter-liveness guardrail (L-C) without a real broker."""
    is_paper = False


def _runner(mode, *, adapter=None):
    from sgt.brokers.costs import DEFAULT_COST_MODEL
    if adapter is None:
        # LIVE requires a live adapter; PAPER/SHADOW a paper/sim one (L-C startup defense).
        adapter = (_LiveSim if mode is Mode.LIVE else SimBroker)("acct", DEFAULT_COST_MODEL, 100_000.0)
    rt = InMemoryLiveSource([], delay_minutes=0, consolidated=True)
    return LiveRunner("tests/fixtures/trader_v0_mechanics.yaml", mode, [_SPEC], source=rt,
                      adapters={"acct": adapter}, risk_config=default_risk_config(CFG),
                      clock=lambda: _TS, sleep=_noop_sleep, run_id=f"{mode.value}-test")


def _write_baseline(status="validated"):
    with get_session() as s:
        registry.upsert_registry(s, config_hash=_HASH, config={}, status=status,
                                 evidence={"decay_baseline": {
                                     "expectancy": 0.01, "ret_std": 0.02, "win_rate": 0.55}})


async def test_live_refuses_without_validated_baseline():
    r = _runner(Mode.LIVE)
    try:
        await r.startup()
        raised = False
    except BaselineMissingError as exc:
        raised = True
        assert _HASH in str(exc) or "baseline" in str(exc).lower()
    assert raised, "LIVE must refuse to start without a validated decay baseline"


async def test_live_starts_with_registry_baseline():
    _write_baseline()
    r = _runner(Mode.LIVE)
    await r.startup()
    assert r._supervisor is not None and "cusum" in r._supervisor.monitors
    assert r._decay is not None and r._decay._flag_only is False


async def test_baseline_from_backtest_builds_a_baseline():
    base = baseline_from_backtest(CFG, dataset(["AAAA", "BBBB"], n_sessions=3),
                                  starting_cash=100_000.0, dd_budget=500.0, allow_unknown_float=True)
    assert base.ret_std > 0 and 0.0 < base.win_rate < 1.0 and base.source == "backtest"


async def test_paper_planted_losing_streak_demotes_and_audits():
    _write_baseline()
    r = _runner(Mode.PAPER)
    await r.startup()
    assert r._decay is not None and r._decay._flag_only is False
    for _ in range(6):
        r._feed_decay({"strategy_id": CFG.id, "ret": -0.5, "pnl": -50.0, "exit_ts": _TS,
                       "realized_slippage": 0.0, "modeled_slippage": 0.0})
    actions = [e["payload"]["action"] for e in r.sink.events if e["kind"] == "decay_action"]
    assert "demote" in actions, f"expected a DEMOTE, saw {actions}"
    audit_kinds = {k for _ts, k, _d in RiskStore("paper").iter_audit()}
    assert "strategy_demoted" in audit_kinds
