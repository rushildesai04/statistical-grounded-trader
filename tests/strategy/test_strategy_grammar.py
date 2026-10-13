"""Grammar validator: rejects each violation (and lists them all), accepts
trader_v0, and produces a key-order-independent config hash."""
from __future__ import annotations

import copy

import pytest
import yaml

from sgt.strategy.loader import config_hash, load_strategy
from sgt.strategy.validate import StrategyValidationError, validate_strategy

_TRADER_V0 = "strategies/trader_v0.yaml"


def _base() -> dict:
    with open(_TRADER_V0, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_trader_v0_loads_and_validates():
    cfg = load_strategy(_TRADER_V0)
    assert cfg.id == "trader_v0"
    assert cfg.trigger.kind == "ema_cross_up"
    assert len(cfg.confirm.signals) == 3 and cfg.confirm.k_of_m == 2


def test_config_hash_is_key_order_independent():
    raw = _base()
    cfg1 = validate_strategy(raw)
    # Shuffle top-level keys; hash must be identical.
    reordered = {k: raw[k] for k in reversed(list(raw))}
    cfg2 = validate_strategy(reordered)
    assert config_hash(cfg1) == config_hash(cfg2)


def test_two_triggers_rejected():
    raw = _base()
    raw["trigger"] = [raw["trigger"], {"kind": "psar_flip", "direction": "up"}]
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("exactly one trigger" in v for v in e.value.violations)


def test_context_condition_cannot_trigger():
    raw = _base()
    raw["trigger"] = {"kind": "session_window_in", "windows": ["rth"]}
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("context" in v or "may not be used as a trigger" in v for v in e.value.violations)


def test_same_family_confirm_rejected():
    raw = _base()
    # macd_line_cross_up is TREND, same family as the ema_cross_up trigger.
    raw["confirm"] = {"k_of_m": 1, "signals": [{"kind": "macd_line_cross_up"}]}
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("different family" in v for v in e.value.violations)


def test_two_same_group_confirms_rejected():
    raw = _base()
    raw["trigger"] = {"kind": "psar_flip", "direction": "up"}  # structure trigger, group None
    raw["confirm"] = {"k_of_m": 1, "signals": [
        {"kind": "macd_line_cross_up"},
        {"kind": "supertrend_state", "state": "up"},
    ]}
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("correlation group" in v for v in e.value.violations)


def test_missing_stop_and_time_stop_rejected_together():
    raw = _base()
    raw["exits"] = {"any_of": [{"kind": "ema_cross_down", "fast": 9, "slow": 24}]}
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("stop_loss_pct" in v for v in e.value.violations)
    assert any("time_stop_minutes" in v for v in e.value.violations)


def test_bad_indicator_params_rejected():
    raw = _base()
    raw["filters"] = [{"kind": "rsi_lt", "n": 1, "value": 70.0}]  # rsi requires n in [2, 500]
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("invalid indicator spec" in v for v in e.value.violations)


def test_bad_macd_cross_params_rejected():
    raw = _base()
    raw["confirm"] = {"k_of_m": 1, "signals": [
        {"kind": "macd_line_cross_up", "fast": 26, "slow": 12}]}  # fast must be < slow
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("invalid indicator spec" in v for v in e.value.violations)


def test_k_of_m_exceeds_signal_count():
    raw = _base()
    raw["confirm"] = {"k_of_m": 5, "signals": [{"kind": "pvt_state", "state": "above_signal"}]}
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("k_of_m" in v for v in e.value.violations)


def test_post_window_requires_extended_hours():
    raw = _base()
    raw["extended_hours"] = False  # but regime still trades after_hours
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("extended_hours is false" in v for v in e.value.violations)
    assert any("session_end.window must be 'rth'" in v for v in e.value.violations)


def test_session_end_window_must_match_extended_hours():
    raw = _base()
    raw["regime"] = [{"kind": "session_window_in", "windows": ["power_hour"]}]  # RTH-only regime
    raw["extended_hours"] = False
    # session_end still says post -> mismatch with an RTH-only, non-extended strategy
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("session_end.window must be 'rth'" in v for v in e.value.violations)


def test_all_violations_listed_at_once():
    raw = _base()
    raw["trigger"] = {"kind": "session_window_in", "windows": ["rth"]}  # context trigger
    raw["exits"] = {"any_of": [{"kind": "ema_cross_down", "fast": 9, "slow": 24}]}  # missing stops
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    # context-trigger + missing stop + missing time-stop all reported together.
    assert len(e.value.violations) >= 3


@pytest.mark.parametrize("fast,slow", [(24, 9), (9, 9)])
def test_cross_requires_fast_lt_slow(fast, slow):
    """F-6 regression: the grammar (not only the search layer) rejects fast >= slow."""
    raw = copy.deepcopy(_base())
    raw["trigger"]["fast"], raw["trigger"]["slow"] = fast, slow
    with pytest.raises(StrategyValidationError) as e:
        validate_strategy(raw)
    assert any("requires fast < slow" in v for v in e.value.violations)


def test_config_hash_is_stable_across_interpreter_hash_seeds():
    """F-11 regression: a frozenset field serialized in hash-seed order made the config
    hash (registry/lockbox/baseline identity) differ between processes."""
    import os
    import subprocess
    import sys

    code = ("from sgt.strategy.loader import load_strategy, config_hash;"
            "print(config_hash(load_strategy('strategies/trader_v0.yaml')))")
    hashes = set()
    for seed in ("1", "7", "8", "11", "13", "21"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True,
                             text=True, check=True, timeout=60)
        hashes.add(out.stdout.strip())
    assert len(hashes) == 1, hashes
