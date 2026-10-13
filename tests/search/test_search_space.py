"""Enumeration: grammar-invalid combos are skipped and counted; the cap is enforced."""
from __future__ import annotations

import pytest

from sgt.search.space import (
    CombinationCapError,
    ConfirmPool,
    SearchSpace,
    SkipCounts,
    enumerate_configs,
)


def test_fast_ge_slow_skipped_and_counted(base_yaml):
    space = SearchSpace(base=base_yaml, axes={"trigger.fast": [9, 24], "trigger.slow": [18, 24]})
    skips = SkipCounts()
    out = list(enumerate_configs(space, skips=skips))
    # (9,18) and (9,24) are valid; (24,18) and (24,24) violate fast<slow.
    assert len(out) == 2
    assert skips.reasons["fast_ge_slow"] == 2
    assert skips.total_yielded == 2
    for cfg, params in out:
        assert cfg.trigger.fast < cfg.trigger.slow
        assert params["trigger.fast"] < params["trigger.slow"]


def test_same_family_confirm_skipped(base_yaml):
    # macd_line_cross_up is TREND, same family as the ema_cross_up trigger -> invalid confirm.
    pool = ConfirmPool(
        signals=[{"kind": "macd_line_cross_up"}, {"kind": "rsi_lt", "n": 14, "value": 100.0}],
        k_of_m=[1], choose=[1])
    space = SearchSpace(base=base_yaml, confirm_pool=pool)
    skips = SkipCounts()
    out = list(enumerate_configs(space, skips=skips))
    assert len(out) == 1  # only the rsi confirm survives
    assert skips.reasons["same_family_confirm"] == 1


def test_combination_cap_enforced(base_yaml):
    space = SearchSpace(
        base=base_yaml,
        axes={"trigger.fast": [5, 9, 12], "trigger.slow": [18, 21, 24]},
        max_combinations=4)
    assert space.raw_grid_size() == 9
    with pytest.raises(CombinationCapError):
        list(enumerate_configs(space))


def test_cap_can_be_raised_explicitly(base_yaml):
    space = SearchSpace(
        base=base_yaml,
        axes={"trigger.fast": [5, 9, 12], "trigger.slow": [18, 21, 24]},
        max_combinations=100)
    out = list(enumerate_configs(space))
    # all 9 have fast<slow, so none are skipped for ordering.
    assert len(out) == 9


def test_keyed_list_path_sets_exit_pct(base_yaml):
    space = SearchSpace(base=base_yaml, axes={"exits.any_of[stop_loss_pct].pct": [2.0, 3.0, 4.0]})
    out = list(enumerate_configs(space))
    pcts = sorted(
        c.pct for cfg, _ in out for c in cfg.exits.any_of if c.kind == "stop_loss_pct")
    assert pcts == [2.0, 3.0, 4.0]
