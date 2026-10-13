"""Plateau selection prefers a broad high region over an isolated spike (plan §10)."""
from __future__ import annotations

from datetime import date

from sgt.search.runner import SearchResult, TrialResult
from sgt.search.select import numeric_axes, plateau_select


def _planted_result() -> SearchResult:
    fasts = [5, 6, 7, 8, 9]
    slows = [20, 21, 22, 23, 24]
    trials: list[TrialResult] = []
    for i, f in enumerate(fasts):
        for j, s in enumerate(slows):
            if (f, s) == (5, 20):
                val = 2.0           # isolated spike
            elif 1 <= i <= 3 and 1 <= j <= 3:
                val = 1.0           # broad 3x3 plateau
            else:
                val = 0.0
            trials.append(TrialResult(
                config=None, config_hash=f"h{i}_{j}",
                params={"trigger.fast": f, "trigger.slow": s},
                returns=[val] * 30,
                metrics={"expectancy": val, "n_trades": 100, "sharpe": val},
                n_trades=100))
    return SearchResult(search_run_id="plant", trials=trials, fold_sessions=[],
                        lockbox_start=date(2025, 1, 1), starting_cash=1.0,
                        n_trials=len(trials))


def test_plateau_center_over_spike():
    result = _planted_result()
    assert set(numeric_axes(result)) == {"trigger.fast", "trigger.slow"}
    choice = plateau_select(result, metric="expectancy", min_trades=1)
    assert choice is not None
    # Centre of the plateau, not the 2.0 spike at (5, 20).
    assert choice.center == {"trigger.fast": 7, "trigger.slow": 22}
    assert choice.cell_metric == 1.0
    assert choice.center != {"trigger.fast": 5, "trigger.slow": 20}


def test_min_trades_floor_excludes_sparse_cells():
    result = _planted_result()
    # Raise the floor above every cell's trade count -> nothing eligible.
    assert plateau_select(result, metric="expectancy", min_trades=1000) is None


def test_render_grid_header_matches_column_count():
    """F-14 regression: header row must have one cell per column plus the corner."""
    import pandas as pd

    from sgt.search.select import render_grid

    df = pd.DataFrame([[0.1, 0.2], [0.3, 0.4]], index=pd.Index([21, 24], name="trigger.slow"),
                      columns=pd.Index([8, 9], name="trigger.fast"))
    lines = [l for l in render_grid(df).splitlines() if l.startswith("|")]
    widths = {l.count("|") for l in lines}
    assert widths == {4}, lines
    assert "trigger.slow" in lines[0] and "trigger.fast" in lines[0]
