"""run_live CLI arg validation: fail-closed gating for LIVE/PAPER before any broker or
feed is constructed."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_live.py"
_spec = importlib.util.spec_from_file_location("run_live", _SCRIPT)
run_live = importlib.util.module_from_spec(_spec)
sys.modules["run_live"] = run_live
_spec.loader.exec_module(run_live)

from sgt.accounts.registry import AccountSpec  # noqa: E402
from sgt.brokers.sim import SimBroker  # noqa: E402
from sgt.core.types import Mode  # noqa: E402

_STRAT = "strategies/trader_v0.yaml"


def _spec(broker: str) -> AccountSpec:
    return AccountSpec("acct", broker, "cash", True, 1.0, 0.0)


def test_default_mode_is_shadow():
    args = run_live.parse_args(["--strategy", _STRAT, "--accounts", "sim1", "--symbols", "AAA"])
    assert args.mode is Mode.SHADOW
    run_live.validate(args)  # shadow needs no risk file / confirm


def test_live_requires_confirm_live():
    args = run_live.parse_args(
        ["--strategy", _STRAT, "--mode", "live", "--accounts", "a", "--symbols", "AAA", "--feed", "sip",
         "--risk", "r.yaml"])
    with pytest.raises(SystemExit):
        run_live.validate(args)


def test_live_requires_sip_feed():
    args = run_live.parse_args(
        ["--strategy", _STRAT, "--mode", "live", "--accounts", "a", "--symbols", "AAA", "--feed", "iex",
         "--risk", "r.yaml", "--confirm-live"])
    with pytest.raises(SystemExit):
        run_live.validate(args)


def test_paper_requires_risk_config():
    args = run_live.parse_args(
        ["--strategy", _STRAT, "--mode", "paper", "--accounts", "a", "--symbols", "AAA", "--feed", "sip"])
    with pytest.raises(SystemExit):
        run_live.validate(args)


def test_live_with_confirm_and_sip_and_risk_passes_validation():
    args = run_live.parse_args(
        ["--strategy", _STRAT, "--mode", "live", "--accounts", "a", "--symbols", "AAA", "--feed", "sip",
         "--risk", "r.yaml", "--confirm-live"])
    run_live.validate(args)  # must not raise


def test_live_refuses_allow_unknown_float():
    """F-8: real capital never trades a float-capped universe without float data."""
    args = run_live.parse_args(["--strategy", _STRAT, "--accounts", "sim1", "--symbols", "AAA",
                                "--mode", "live", "--feed", "sip", "--risk", "r.yaml",
                                "--confirm-live", "--allow-unknown-float"])
    with pytest.raises(SystemExit):
        run_live.validate(args)


def test_symbols_are_required_and_normalized():
    """F-10: a runner with no watchlist would subscribe to nothing."""
    with pytest.raises(SystemExit):
        run_live.parse_args(["--strategy", _STRAT, "--accounts", "sim1"])
    args = run_live.parse_args(["--strategy", _STRAT, "--accounts", "sim1", "--symbols", "wff, ymat"])
    assert args.symbols == ("WFF", "YMAT")


# ------------------------------------------------------- L-C: explicit broker strings + liveness
def test_plain_alpaca_broker_is_rejected():
    """Ambiguous 'alpaca' (endpoint would depend on mode) is refused with a migration hint."""
    for mode in (Mode.SHADOW, Mode.PAPER, Mode.LIVE):
        with pytest.raises(SystemExit) as ei:
            run_live._build_adapter(_spec("alpaca"), mode, 100_000.0)
        assert "alpaca_paper" in str(ei.value) and "alpaca_live" in str(ei.value)


def test_paper_mode_refuses_alpaca_live():
    with pytest.raises(SystemExit):
        run_live._build_adapter(_spec("alpaca_live"), Mode.PAPER, 100_000.0)


def test_shadow_mode_refuses_alpaca_live():
    with pytest.raises(SystemExit):
        run_live._build_adapter(_spec("alpaca_live"), Mode.SHADOW, 100_000.0)


def test_live_mode_refuses_alpaca_paper():
    with pytest.raises(SystemExit):
        run_live._build_adapter(_spec("alpaca_paper"), Mode.LIVE, 100_000.0)


def test_live_mode_refuses_sim_adapter():
    with pytest.raises(SystemExit):
        run_live._build_adapter(_spec("sim"), Mode.LIVE, 100_000.0)


def test_sim_adapter_builds_in_shadow():
    ad = run_live._build_adapter(_spec("sim"), Mode.SHADOW, 100_000.0)
    assert isinstance(ad, SimBroker) and ad.is_paper is True
