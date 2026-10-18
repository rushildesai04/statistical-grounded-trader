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


@pytest.mark.parametrize("broker,live_ok,paper_ok", [
    ("schwab_live", True, False), ("snaptrade_live", True, False),
    ("ibkr_live", True, False), ("ibkr_paper", False, True),
    ("robinhood_snaptrade", True, False),
])
def test_new_broker_liveness_rules(broker, live_ok, paper_ok, monkeypatch):
    """Connector wiring: every new broker string obeys the mode/liveness rules (L-C)."""
    from sgt.accounts.registry import AccountSpec
    from sgt.core.types import Mode

    spec = AccountSpec("acct1", broker, "cash", True, 1.0, 0.0)
    for name in ("schwab", "snaptrade", "ibkr"):
        monkeypatch.setenv(f"SGT_SECRET_{name.upper()}_ACCOUNT_REF_ACCT1",
                           "1234" if name == "schwab" else "ref")
    for k in ("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY", "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET",
              "SCHWAB_APP_KEY", "SCHWAB_APP_SECRET"):
        monkeypatch.setenv(f"SGT_SECRET_{k}", "x")
    for mode, ok in ((Mode.LIVE, live_ok), (Mode.PAPER, paper_ok)):
        if ok:
            adapter = run_live._build_adapter(spec, mode, 10_000.0)
            assert adapter.account_id == "acct1"
            assert adapter.is_paper is (mode is Mode.PAPER)
        else:
            with pytest.raises(SystemExit):
                run_live._build_adapter(spec, mode, 10_000.0)


def test_robinhood_snaptrade_is_alert_execution_with_broker_positions(monkeypatch):
    from sgt.accounts.registry import AccountSpec
    from sgt.brokers.composite import AlertWithBrokerPositions
    from sgt.core.types import Mode

    monkeypatch.setenv("SGT_SECRET_SNAPTRADE_ACCOUNT_REF_RH1", "st-acct")
    for k in ("SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY", "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET"):
        monkeypatch.setenv(f"SGT_SECRET_{k}", "x")
    adapter = run_live._build_adapter(AccountSpec("rh1", "robinhood_snaptrade", "cash", True, 1.0, 0.0),
                                      Mode.LIVE, 0.0)
    assert isinstance(adapter, AlertWithBrokerPositions)


def test_missing_account_ref_fails_closed(monkeypatch):
    from sgt.accounts.registry import AccountSpec
    from sgt.core.secrets import MissingSecretError
    from sgt.core.types import Mode

    monkeypatch.delenv("SGT_SECRET_SCHWAB_ACCOUNT_REF_NOREF", raising=False)
    with pytest.raises(MissingSecretError):
        run_live._build_adapter(AccountSpec("noref", "schwab_live", "cash", True, 1.0, 0.0), Mode.LIVE, 0.0)


def test_live_refuses_research_artifact_flag():
    """R2-F1: a LIVE run may never load a quarantined / research model artifact."""
    args = run_live.parse_args(["--strategy", "x.yaml", "--mode", "live", "--accounts", "a",
                                "--feed", "sip", "--risk", "r.yaml", "--confirm-live",
                                "--symbols", "AAA", "--allow-research-artifact"])
    with pytest.raises(SystemExit, match="allow-research-artifact"):
        run_live.validate(args)
