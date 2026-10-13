"""Lockbox: one evaluation per config; a second opening raises (plan §10)."""
from __future__ import annotations

import pytest

from sgt.search.lockbox import LockboxAlreadyOpened, open_lockbox
from sgt.search.runner import run_search
from sgt.search.space import SearchSpace


def test_second_opening_raises(make_config, base_yaml, make_bars):
    cfg = make_config()
    bars, days = make_bars(symbol="AAA", n_sessions=6, seed=1)
    bars_by_symbol = {"AAA": bars}
    lockbox_start = days[4]

    # Populate the trial lineage so the DSR trial count is real.
    space = SearchSpace(base=base_yaml, axes={"trigger.fast": [9]})
    run_search(space, bars_by_symbol, folds=2, lockbox_start=lockbox_start,
               search_run_id="lb", persist=True, allow_unknown_float=True)

    report = open_lockbox(cfg, bars_by_symbol, lockbox_start, search_run_id="lb", allow_unknown_float=True)
    assert report.n_trades >= 2
    assert isinstance(report.accepted, bool)

    with pytest.raises(LockboxAlreadyOpened):
        open_lockbox(cfg, bars_by_symbol, lockbox_start, search_run_id="lb", allow_unknown_float=True)
