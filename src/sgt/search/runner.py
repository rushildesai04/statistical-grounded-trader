"""Walk-forward search runner (plan §10).

For every grammar-valid config in a :class:`SearchSpace`, ``run_search`` evaluates
out-of-sample performance by SESSION-level expanding walk-forward and records one
trial per config (so the Deflated Sharpe Ratio sees the true trial count).

The leakage-critical rule, enforced here:

- Sessions on or after ``lockbox_start`` are removed up front and NEVER passed to the
  backtester — the held-out window is untouched by search.
- For each test fold, the backtester is fed the test sessions PLUS every prior
  session for indicator warm-up, but NOTHING after the test block (no future
  leakage). Only trades whose ENTRY falls in a test session are counted toward OOS
  metrics; warm-up-session trades are discarded.

Evaluation is deterministic: ids and timestamps come from content hashes and bars, so
``workers=1`` and ``workers=N`` produce byte-identical results (the pool preserves
submission order).
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from sgt.brokers.costs import CostModel
from sgt.core.types import Bar
from sgt.db.session import get_session
from sgt.engine.backtest import run_backtest
from sgt.learning.validation import (
    expectancy,
    max_drawdown,
    profit_factor,
    purged_walk_forward_splits,
)
from sgt.search import registry
from sgt.search.space import SearchSpace, SkipCounts, enumerate_configs
from sgt.strategy.loader import config_hash as compute_config_hash
from sgt.strategy.runtime import EntryOverride
from sgt.strategy.schema import StrategyConfig

FoldSessions = list[tuple[tuple[date, ...], tuple[date, ...]]]


@dataclass(slots=True)
class OOSResult:
    returns: np.ndarray
    trades: pd.DataFrame
    n_entries: int


@dataclass(slots=True)
class TrialResult:
    config: StrategyConfig
    config_hash: str
    params: dict
    returns: list[float]
    metrics: dict
    n_trades: int


@dataclass(slots=True)
class SearchResult:
    search_run_id: str
    trials: list[TrialResult]
    fold_sessions: FoldSessions
    lockbox_start: date
    starting_cash: float
    n_trials: int = 0
    skips: dict = field(default_factory=dict)

    def by_hash(self, config_hash: str) -> TrialResult | None:
        for t in self.trials:
            if t.config_hash == config_hash:
                return t
        return None

    def best(self, metric: str = "expectancy", *, min_trades: int = 1) -> TrialResult | None:
        eligible = [t for t in self.trials if t.n_trades >= min_trades]
        if not eligible:
            return None
        return max(eligible, key=lambda t: t.metrics.get(metric, float("-inf")))


def build_fold_sessions(all_sessions: Sequence[date], folds: int, embargo: int) -> FoldSessions:
    """Expanding walk-forward folds over session dates (plan §10).

    Each fold is ``(warmup_sessions, test_sessions)``: the test block plus every
    session strictly before it for indicator warm-up. ``embargo`` (>=1 session) is
    applied via :func:`purged_walk_forward_splits` so folds whose train window would
    collapse are skipped."""
    ordered = sorted(all_sessions)
    out: FoldSessions = []
    for _train_idx, test_idx in purged_walk_forward_splits(ordered, folds, embargo=embargo):
        test_pos = sorted(int(i) for i in test_idx)
        test = tuple(ordered[i] for i in test_pos)
        warmup = tuple(ordered[i] for i in range(test_pos[0]))
        out.append((warmup, test))
    return out


def _fold_bars(
    bars_by_symbol: Mapping[str, Sequence[Bar]], warmup: frozenset, test: frozenset
) -> tuple[dict[str, list[Bar]], dict, set]:
    keep = warmup | test
    fold: dict[str, list[Bar]] = {}
    ts_to_session: dict = {}
    for sym, bars in bars_by_symbol.items():
        sel = [b for b in bars if b.session_date in keep]
        if sel:
            fold[sym] = sel
            for b in sel:
                ts_to_session[b.ts] = b.session_date
    return fold, ts_to_session, set(test)


def evaluate_oos(
    config: StrategyConfig,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    fold_sessions: FoldSessions,
    *,
    starting_cash: float,
    run_tag: str,
    entry_override: EntryOverride | None = None,
    cost_model: CostModel | None = None,
    collect_trades: bool = False,
    reference: object = None,
    allow_unknown_float: bool = False,
) -> OOSResult:
    """Run the config over every fold and return only its in-fold (OOS) trades."""
    frames: list[pd.DataFrame] = []
    returns: list[float] = []
    for i, (warmup, test) in enumerate(fold_sessions):
        fold, ts_to_session, test_set = _fold_bars(
            bars_by_symbol, frozenset(warmup), frozenset(test))
        if not fold:
            continue
        res = run_backtest(
            config, fold, starting_cash=starting_cash, run_id=f"{run_tag}:{i}",
            entry_override=entry_override, cost_model=cost_model,
            reference=reference, allow_unknown_float=allow_unknown_float)
        trades = res.trades
        if trades.empty:
            continue
        ent = pd.to_datetime(trades["entry_ts"], utc=True)
        sess = ent.map(lambda t: ts_to_session.get(t.to_pydatetime()))
        mask = sess.isin(test_set)
        infold = trades.loc[mask]
        if infold.empty:
            continue
        returns.extend(float(x) for x in infold["ret"].to_numpy(dtype=float))
        if collect_trades:
            fr = infold.copy()
            fr["session_date"] = sess.loc[mask].to_numpy()
            fr["fold"] = i
            frames.append(fr)
    trades_df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    arr = np.asarray(returns, dtype=float)
    return OOSResult(returns=arr, trades=trades_df, n_entries=int(arr.size))


def oos_metrics(returns: np.ndarray) -> dict:
    n = int(returns.size)
    if n == 0:
        return {"n_trades": 0, "expectancy": 0.0, "profit_factor": 0.0,
                "max_drawdown": 0.0, "win_rate": 0.0, "sharpe": None}
    std = float(returns.std(ddof=1)) if n >= 2 else 0.0
    exp = float(expectancy(returns))
    return {
        "n_trades": n,
        "expectancy": exp,
        "profit_factor": profit_factor(returns),
        "max_drawdown": float(max_drawdown(returns)),
        "win_rate": float((returns > 0).mean()),
        "sharpe": (exp / std) if std > 0 else None,
    }


# ---------------------------------------------------------------- parallel plumbing

_CTX: dict = {}


def _init_worker(bars_by_symbol, fold_sessions, starting_cash, reference=None,
                 allow_unknown_float=False) -> None:
    _CTX["bars"] = bars_by_symbol
    _CTX["folds"] = fold_sessions
    _CTX["cash"] = starting_cash
    _CTX["reference"] = reference
    _CTX["allow_unknown_float"] = allow_unknown_float


def _eval_one(item: tuple[StrategyConfig, dict]) -> TrialResult:
    config, params = item
    cfg_hash = compute_config_hash(config)
    oos = evaluate_oos(
        config, _CTX["bars"], _CTX["folds"], starting_cash=_CTX["cash"],
        run_tag=f"search:{cfg_hash}", reference=_CTX.get("reference"),
        allow_unknown_float=_CTX.get("allow_unknown_float", False))
    metrics = oos_metrics(oos.returns)
    return TrialResult(
        config=config, config_hash=cfg_hash, params=params,
        returns=[float(x) for x in oos.returns], metrics=metrics,
        n_trades=metrics["n_trades"])


def run_search(
    space: SearchSpace,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    folds: int,
    lockbox_start: date,
    workers: int = 1,
    seed: int = 0,
    search_run_id: str = "search",
    starting_cash: float = 100_000.0,
    embargo: int = 1,
    persist: bool = True,
    reference: object = None,
    allow_unknown_float: bool = False,
) -> SearchResult:
    """Enumerate the space and evaluate every config by walk-forward OOS.

    ``lockbox_start`` sessions are excluded from all evaluation. ``persist`` writes one
    idempotent trial row per config (and a ``candidate`` registry row) — re-running
    with the same ``search_run_id`` does not double-count trials."""
    if folds < 1:
        raise ValueError("folds must be >= 1")
    if embargo < 1:
        raise ValueError("embargo must be >= 1 session (plan §10)")

    research = {
        sym: [b for b in bars if b.session_date < lockbox_start]
        for sym, bars in bars_by_symbol.items()
    }
    research = {sym: bars for sym, bars in research.items() if bars}
    all_sessions = sorted({b.session_date for bars in research.values() for b in bars})
    fold_sessions = build_fold_sessions(all_sessions, folds, embargo)

    skips = SkipCounts()
    items = list(enumerate_configs(space, skips=skips))

    if workers <= 1:
        _init_worker(research, fold_sessions, starting_cash, reference, allow_unknown_float)
        trials = [_eval_one(it) for it in items]
    else:
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_init_worker,
            initargs=(research, fold_sessions, starting_cash, reference, allow_unknown_float),
        ) as ex:
            trials = list(ex.map(_eval_one, items))

    if persist:
        _persist(search_run_id, trials, space)

    return SearchResult(
        search_run_id=search_run_id, trials=trials, fold_sessions=fold_sessions,
        lockbox_start=lockbox_start, starting_cash=starting_cash,
        n_trials=len(trials), skips=skips.as_dict())


def _persist(search_run_id: str, trials: list[TrialResult], space: SearchSpace) -> None:
    with get_session() as session:
        for t in trials:
            registry.record_trial(
                session, search_run_id=search_run_id, config_hash=t.config_hash,
                params=t.params, metrics=t.metrics, n_trades=t.n_trades)
            registry.upsert_registry(
                session, config_hash=t.config_hash,
                config=t.config.model_dump(mode="json"), status="candidate")
