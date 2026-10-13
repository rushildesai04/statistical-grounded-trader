"""Plateau selection and heatmap rendering (plan §10).

A parameter search that picks the single best-scoring cell overfits: the global max
of a noisy grid is usually a spike that does not reproduce out of sample. Instead we
score each cell by the mean of itself and its immediate neighbours (Chebyshev radius 1)
minus a dispersion penalty, and select the centre of the best broad plateau. A lone
high cell surrounded by poor neighbours is penalised; a wide region of consistently
good cells wins.

Heatmaps are rendered as plain-text / markdown tables (no plotting dependency), so a
candidate card is a self-contained document.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
import pandas as pd

from sgt.search.runner import SearchResult, TrialResult


@dataclass(slots=True)
class PlateauChoice:
    trial: TrialResult
    center: dict  # axis -> value at the plateau centre
    score: float
    cell_metric: float
    neighborhood: int


def numeric_axes(result: SearchResult) -> list[str]:
    """Axis paths whose values are numeric across every trial (grid dimensions)."""
    if not result.trials:
        return []
    keys = set(result.trials[0].params)
    for t in result.trials:
        keys &= set(t.params)
    out = []
    for k in sorted(keys):
        vals = [t.params.get(k) for t in result.trials]
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            out.append(k)
    return out


def _grid(result: SearchResult, axes: Sequence[str], metric: str):
    """Map axis-index coordinate -> (metric value, trial). Returns (coords, axis_values)."""
    axis_values = [sorted({t.params[a] for t in result.trials}) for a in axes]
    index_of = [{v: i for i, v in enumerate(vals)} for vals in axis_values]
    cells: dict[tuple[int, ...], tuple[float, TrialResult]] = {}
    for t in result.trials:
        if any(a not in t.params for a in axes):
            continue
        coord = tuple(index_of[d][t.params[a]] for d, a in enumerate(axes))
        val = t.metrics.get(metric)
        if val is None:
            continue
        # If multiple trials share a coordinate (other axes vary), keep the best.
        if coord not in cells or val > cells[coord][0]:
            cells[coord] = (float(val), t)
    return cells, axis_values


def plateau_select(
    result: SearchResult,
    axes: Sequence[str] | None = None,
    *,
    metric: str = "expectancy",
    radius: int = 1,
    dispersion_penalty: float = 1.0,
    min_trades: int = 1,
) -> PlateauChoice | None:
    """Pick the plateau centre (not the global max cell) over ``axes``.

    Only cells whose own trial has ``>= min_trades`` OOS trades are eligible centres.
    A cell's score is ``mean(neighbourhood) - dispersion_penalty * std(neighbourhood)``.
    Returns None when nothing is eligible."""
    use_axes = list(axes) if axes is not None else numeric_axes(result)
    if not use_axes:
        return None
    cells, axis_values = _grid(result, use_axes, metric)
    if not cells:
        return None
    shape = tuple(len(v) for v in axis_values)

    best: PlateauChoice | None = None
    for coord, (cell_val, trial) in sorted(cells.items()):
        if trial.n_trades < min_trades:
            continue
        neigh = _neighborhood(cells, coord, shape, radius)
        vals = np.asarray([v for v, _ in neigh], dtype=float)
        score = float(vals.mean() - dispersion_penalty * (vals.std() if vals.size > 1 else 0.0))
        center = {a: axis_values[d][coord[d]] for d, a in enumerate(use_axes)}
        if best is None or score > best.score or (
            score == best.score and trial.metrics.get(metric, 0.0) > best.cell_metric
        ):
            best = PlateauChoice(trial=trial, center=center, score=score,
                                 cell_metric=cell_val, neighborhood=int(vals.size))
    return best


def _neighborhood(cells, coord, shape, radius):
    offsets = itertools.product(*[range(-radius, radius + 1) for _ in shape])
    out = []
    for off in offsets:
        nb = tuple(coord[d] + off[d] for d in range(len(shape)))
        if all(0 <= nb[d] < shape[d] for d in range(len(shape))) and nb in cells:
            out.append(cells[nb])
    return out


def metric_grid(result: SearchResult, axis_x: str, axis_y: str, metric: str = "expectancy") -> pd.DataFrame:
    """Pivot table of ``metric`` over two axes (rows=axis_y, cols=axis_x)."""
    rows: list[dict[str, Any]] = []
    for t in result.trials:
        if axis_x in t.params and axis_y in t.params:
            rows.append({axis_x: t.params[axis_x], axis_y: t.params[axis_y],
                         "metric": t.metrics.get(metric)})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.pivot_table(index=axis_y, columns=axis_x, values="metric", aggfunc="max")


def render_grid(df: pd.DataFrame, *, title: str = "", fmt: str = "{:.4f}") -> str:
    """Markdown table of a metric grid (NaN cells shown as '-')."""
    if df.empty:
        return "_(no grid)_"
    cols = list(df.columns)
    corner = f"{df.index.name or ''} \\ {df.columns.name or ''}".strip()
    head = "| " + " | ".join([corner, *[str(c) for c in cols]]) + " |"
    sep = "|" + "|".join(["---"] * (len(cols) + 1)) + "|"
    lines = [f"**{title}**" if title else "", head, sep]
    for idx, row in df.iterrows():
        cells = [fmt.format(v) if pd.notna(v) else "-" for v in row.to_numpy()]
        lines.append("| " + " | ".join([str(idx), *cells]) + " |")
    return "\n".join(l for l in lines if l != "" or title == "")
