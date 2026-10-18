"""Learning-from-results diagnostics (plan §9, §10, §11.2).

Codifies the weekly "learn from results" exploration into tested library code. The
pipeline answers four questions, conservatively and reproducibly:

1. ``cost_attribution`` — is there a *gross* edge at all, and do costs eat it? Runs
   the strategy and its ``take_every`` benchmark with real and zero-cost models.
2. ``signal_outcomes`` — for every trigger candidate, the realistic forward outcome
   (enter the bar AFTER the signal at open + half-spread, exit ``h`` bars later at
   close - half-spread, never across a session boundary) plus its point-in-time
   context features. No look-ahead: the entry bar is strictly after the signal bar.
3. ``bucket_table`` / ``stable_buckets`` — where (if anywhere) the net edge lives,
   and whether a bucket is positive in BOTH halves of the data (not a fluke).
4. ``learned_filter_walkforward`` — does a meta-filter, trained only on earlier
   sessions and scored on later unseen ones, create edge in its top decile?

Nothing here changes live trading. It produces evidence; humans approve changes
(plan §11.2/§11.3). Reporting (text recommendations + markdown) lives in
``sgt.learning.diagnostics_report`` to keep each module focused and under budget.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from sgt.brokers.costs import CostModel
from sgt.core.types import Bar
from sgt.engine.backtest import run_backtest
from sgt.learning.meta_filter import GBMMetaFilter, LogisticMetaFilter
from sgt.strategy.runtime import EntryOverride
from sgt.strategy.schema import StrategyConfig
from sgt.strategy.validate import validate_strategy

BarLoader = Callable[[str], Sequence[Bar]]

# Columns emitted by ``signal_outcomes`` that do not depend on the horizon set.
_BASE_COLUMNS = (
    "symbol", "ts", "session_date", "minute_of_day", "price", "segment",
    "mfe30", "mae30", "cost_rt",
    "rsi", "ema_spread", "dist_hod", "gap", "pvt_above", "psar_up",
    "free_float", "dilution", "bar_volume", "relvol_value", "relvol_cum",
)


def _zero_cost_model() -> CostModel:
    """A cost model that charges nothing: half-spread 0 in every bucket, no drag."""
    return CostModel(hs_under_1=0.0, hs_1_to_5=0.0, hs_5_to_20=0.0, hs_over_20=0.0,
                     participation_drag_k=0.0, low_float_mult=1.0)


# ---------------------------------------------------------------- cost attribution


@dataclass(frozen=True)
class CostAttribution:
    """Gross-vs-net expectancy for the config and its ``take_every`` benchmark.

    ``table`` is indexed by variant (``"config"``, ``"take_every"``) with columns
    ``gross_trades, gross_exp, gross_net_usd, net_trades, net_exp, net_net_usd,
    cost_drag`` (``cost_drag = gross_exp - net_exp``, the per-trade return the cost
    model removes). ``take_every_candidates`` is the full trigger set from the
    widened ``take_every`` run — the input to :func:`signal_outcomes`.
    """

    table: pd.DataFrame
    take_every_candidates: pd.DataFrame


def _widen_universe(config: StrategyConfig) -> StrategyConfig:
    """A copy of ``config`` whose universe admits everything, so ``take_every`` can
    study every trigger regardless of price/float/rel-vol gating."""
    raw = config.model_dump(mode="json")
    raw["universe"]["float_max"] = 1.0e12
    raw["universe"]["price_max"] = 1.0e6
    raw["universe"]["rel_vol_min"] = 0.0
    return validate_strategy(raw)


def cost_attribution(
    config: StrategyConfig,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    reference: object = None,
    allow_unknown_float: bool = False,
    cost_model: CostModel | None = None,
    starting_cash: float = 25_000.0,
) -> CostAttribution:
    """Run the config and its ``take_every`` benchmark, each with real and zero costs.

    The ``config`` variant uses the supplied ``cost_model`` (real) vs a zero-cost
    model (gross). The ``take_every`` variant widens the universe and admits unknown
    float so every trigger is counted — this is the signal's intrinsic gross edge.
    """
    real = cost_model or CostModel()
    zero = _zero_cost_model()
    wide = _widen_universe(config)

    def _run(cfg: StrategyConfig, cm: CostModel, override: EntryOverride | None,
             unknown: bool, tag: str):
        return run_backtest(cfg, bars_by_symbol, starting_cash=starting_cash,
                            run_id=tag, reference=reference, entry_override=override,
                            cost_model=cm, allow_unknown_float=unknown)

    cfg_net = _run(config, real, None, allow_unknown_float, "cost_attr:config:net")
    cfg_gross = _run(config, zero, None, allow_unknown_float, "cost_attr:config:gross")
    te = EntryOverride("take_every")
    te_net = _run(wide, real, te, True, "cost_attr:take_every:net")
    te_gross = _run(wide, zero, te, True, "cost_attr:take_every:gross")

    rows = []
    for name, gross, net in (("config", cfg_gross, cfg_net),
                             ("take_every", te_gross, te_net)):
        g, n = gross.summary, net.summary
        rows.append({
            "variant": name,
            "gross_trades": int(g["n_trades"]), "gross_exp": float(g["expectancy"]),
            "gross_net_usd": float(g["net_pnl"]),
            "net_trades": int(n["n_trades"]), "net_exp": float(n["expectancy"]),
            "net_net_usd": float(n["net_pnl"]),
            "cost_drag": float(g["expectancy"]) - float(n["expectancy"]),
        })
    table = pd.DataFrame(rows).set_index("variant")
    return CostAttribution(table=table, take_every_candidates=te_net.candidates)


# ------------------------------------------------------------------ signal outcomes


def _by_prefix_suffix(features: Mapping[str, Any], prefix: str, suffix: str) -> dict[str, float]:
    """All ``{key: value}`` whose key starts with ``prefix`` and ends with ``suffix``."""
    return {k: features[k] for k in features
            if k.startswith(prefix) and k.endswith(suffix)}


def _ema_spread(features: Mapping[str, Any]) -> float:
    """``(fast_ema - slow_ema) / close`` where fast is the smallest-period EMA.

    EMA periods are parsed from the feature key ``ema(n=<p>).value``; with fewer
    than two EMAs or no close, the spread is undefined (NaN)."""
    emas: list[tuple[int, float]] = []
    for key, val in _by_prefix_suffix(features, "ema(n=", ".value").items():
        try:
            n = int(key[len("ema(n="):key.index(")")])
        except (ValueError, IndexError):
            continue
        emas.append((n, float(val)))
    close = features.get("close")
    if len(emas) < 2 or not close:
        return float("nan")
    emas.sort()
    fast, slow = emas[0][1], emas[-1][1]
    return (fast - slow) / float(close)


def _first(features: Mapping[str, Any], prefix: str, suffix: str) -> float:
    found = _by_prefix_suffix(features, prefix, suffix)
    if not found:
        return float("nan")
    # Deterministic: smallest key when several parameterizations are present.
    return float(found[min(found)])


def _extract_context(features: Mapping[str, Any]) -> dict[str, float]:
    """Pull the study's context features out of a candidate's feature snapshot.

    Every value is read by key/prefix; anything absent becomes NaN (never 0)."""
    def num(key: str) -> float:
        v = features.get(key)
        return float(v) if v is not None else float("nan")

    pvt = _by_prefix_suffix(features, "pvt(", ".pvt")
    pvt_sig = _by_prefix_suffix(features, "pvt(", ".signal")
    pvt_above = float("nan")
    if pvt and pvt_sig:
        pvt_above = 1.0 if float(pvt[min(pvt)]) > float(pvt_sig[min(pvt_sig)]) else 0.0

    psar = _by_prefix_suffix(features, "psar(", ".trend")
    psar_up = 1.0 if (psar and float(psar[min(psar)]) > 0) else (0.0 if psar else float("nan"))

    return {
        "rsi": _first(features, "rsi(", ".value"),
        "ema_spread": _ema_spread(features),
        "dist_hod": num("dist_hod_pct"),
        "gap": num("gap_pct"),
        "pvt_above": pvt_above,
        "psar_up": psar_up,
        "free_float": num("free_float"),
        "dilution": num("dilution_flag"),
        "bar_volume": num("bar_volume"),
        "relvol_value": _first(features, "relvol(", ".value"),
        "relvol_cum": _first(features, "relvol(", ".cum"),
    }


def _forward_outcome(bars: Sequence[Bar], i: int, horizons: Sequence[int],
                     cost_model: CostModel) -> dict[str, float] | None:
    """Realistic forward outcome entering at ``bars[i]`` (the bar AFTER the signal).

    Entry fills at ``open * (1 + half_spread)``; each horizon exit fills at
    ``close * (1 - half_spread)`` of the bar ``h`` ahead, clamped to the last bar of
    the SAME session (no cross-session exits). Returns None if the entry bar is not
    in the same session as the signal bar (overnight gap -> no trade)."""
    entry_bar = bars[i]
    if entry_bar.session_date != bars[i - 1].session_date:
        return None
    hs_in = cost_model.half_spread_pct(entry_bar.open)
    entry = entry_bar.open * (1.0 + hs_in)
    out: dict[str, float] = {"price": float(entry_bar.open), "cost_rt": 2.0 * hs_in}
    for h in horizons:
        j = min(i + h, len(bars) - 1)
        while bars[j].session_date != entry_bar.session_date:
            j -= 1
        exit_close = bars[j].close
        hs_out = cost_model.half_spread_pct(exit_close)
        out[f"net{h}"] = exit_close * (1.0 - hs_out) / entry - 1.0
        out[f"gross{h}"] = exit_close / entry_bar.open - 1.0
    window = [b for b in bars[i:min(i + 30, len(bars))]
              if b.session_date == entry_bar.session_date]
    out["mfe30"] = max(b.high for b in window) / entry_bar.open - 1.0
    out["mae30"] = min(b.low for b in window) / entry_bar.open - 1.0
    return out


def signal_outcomes(
    candidates_df: pd.DataFrame,
    bar_loader: BarLoader,
    *,
    horizons: Sequence[int] = (5, 15, 30),
    cost_model: CostModel,
) -> pd.DataFrame:
    """One row per trigger candidate with its realistic forward outcome + context.

    ``candidates_df`` carries ``ts, symbol, features`` (the backtest candidate frame);
    EVERY trigger is studied, taken or skipped. ``bar_loader(symbol)`` returns that
    symbol's time-ordered bars (loaded once per symbol). The entry bar is strictly
    after the signal bar (no look-ahead) and exits never cross a session boundary.
    """
    if candidates_df.empty:
        return pd.DataFrame(columns=_outcome_columns(horizons))
    horizons = tuple(int(h) for h in horizons)
    rows: list[dict[str, float]] = []
    for symbol, grp in candidates_df.groupby("symbol", sort=True):
        bars = list(bar_loader(str(symbol)))
        if not bars:
            continue
        ts_list = [b.ts for b in bars]
        for _, cand in grp.iterrows():
            sig_ts = cand.ts.to_pydatetime() if hasattr(cand.ts, "to_pydatetime") else cand.ts
            i = bisect.bisect_right(ts_list, sig_ts)  # first bar strictly after signal
            if i == 0 or i >= len(bars):
                continue
            fwd = _forward_outcome(bars, i, horizons, cost_model)
            if fwd is None:
                continue
            features = cand.features if isinstance(cand.features, Mapping) else {}
            mod = features.get("minute_of_day")
            row: dict[str, Any] = {
                "symbol": str(symbol),
                "ts": cand.ts,
                "session_date": bars[i].session_date,
                "minute_of_day": int(mod) if mod is not None else bars[i - 1].minute_of_day,
                "segment": bars[i].segment.value,
            }
            row.update(fwd)
            row.update(_extract_context(features))
            rows.append(row)
    return pd.DataFrame(rows, columns=_outcome_columns(horizons))


def _outcome_columns(horizons: Sequence[int]) -> list[str]:
    hcols: list[str] = []
    for h in horizons:
        hcols.extend((f"gross{h}", f"net{h}"))
    base = list(_BASE_COLUMNS)
    # Insert horizon columns right after the identifying/segment columns.
    cut = base.index("mfe30")
    return base[:cut] + hcols + base[cut:]


# --------------------------------------------------------------------- bucketing


def bucket_table(
    outcomes: pd.DataFrame,
    by: str,
    bins: Sequence[float] | None = None,
    metric: str = "net30",
) -> pd.DataFrame:
    """Mean ``metric`` per bucket of ``by`` (continuous -> ``bins`` via ``pd.cut``).

    Columns: ``n`` (count), ``mean`` (mean metric), ``win`` (fraction > 0),
    ``cost_rt`` (mean round-trip cost). Ordered by bucket."""
    cols = ["n", "mean", "win", "cost_rt"]
    if outcomes.empty or by not in outcomes.columns or metric not in outcomes.columns:
        return pd.DataFrame(columns=cols)
    key = outcomes[by] if bins is None else pd.cut(outcomes[by], bins=list(bins))
    grp = outcomes.groupby(key, observed=True)
    table = pd.DataFrame({
        "n": grp[metric].size(),
        "mean": grp[metric].mean(),
        "win": grp[metric].apply(lambda s: float((s > 0).mean())),
        "cost_rt": grp["cost_rt"].mean() if "cost_rt" in outcomes.columns else np.nan,
    })
    return table[cols]


BucketSpec = tuple[str, Sequence[float] | None]


def stable_buckets(
    outcomes: pd.DataFrame,
    specs: Sequence[BucketSpec],
    *,
    min_n: int = 30,
    metric: str = "net30",
) -> pd.DataFrame:
    """Per bucket: mean ``metric`` and ``n`` in each chronological half of the data.

    A bucket is ``stable`` only if it is net-positive in BOTH halves with ``n >=
    min_n`` in each — the exploration's fluke guard. Halves are split on the sorted
    unique session dates (first half vs. the rest)."""
    cols = ["spec", "bucket", "n_h1", "net_h1", "n_h2", "net_h2", "stable"]
    if outcomes.empty or "session_date" not in outcomes.columns:
        return pd.DataFrame(columns=cols)
    sessions = sorted(pd.unique(outcomes["session_date"]))
    mid = len(sessions) // 2
    first = set(sessions[:mid])
    h1 = outcomes[outcomes["session_date"].isin(first)]
    h2 = outcomes[~outcomes["session_date"].isin(first)]

    rows: list[dict[str, Any]] = []
    for by, bins in specs:
        if by not in outcomes.columns:
            continue

        def keyed(df: pd.DataFrame) -> pd.Series:
            return df[by] if bins is None else pd.cut(df[by], bins=list(bins))

        s1, s2 = keyed(h1), keyed(h2)
        n1 = h1.groupby(s1, observed=True)[metric].size()
        m1 = h1.groupby(s1, observed=True)[metric].mean()
        n2 = h2.groupby(s2, observed=True)[metric].size()
        m2 = h2.groupby(s2, observed=True)[metric].mean()
        buckets = sorted(set(n1.index) | set(n2.index), key=str)
        for b in buckets:
            cn1, cm1 = int(n1.get(b, 0)), float(m1.get(b, float("nan")))
            cn2, cm2 = int(n2.get(b, 0)), float(m2.get(b, float("nan")))
            stable = cn1 >= min_n and cn2 >= min_n and cm1 > 0 and cm2 > 0
            rows.append({"spec": by, "bucket": str(b), "n_h1": cn1, "net_h1": cm1,
                         "n_h2": cn2, "net_h2": cm2, "stable": bool(stable)})
    return pd.DataFrame(rows, columns=cols)


# ------------------------------------------------------------- learned filter WFO


_WFO_FEATURES = ("minute", "log_price", "rsi", "spread", "dist_hod", "gap",
                 "pvt_above", "psar_up", "log_float", "dil", "log_vol", "cost")


def _wfo_design(outcomes: pd.DataFrame, target: str) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Build the (X, y, session_date) design matrix, imputing exactly as the
    exploration did. Index is preserved so a caller/spy can map rows to sessions."""
    d = outcomes.dropna(subset=["rsi", "minute_of_day", "price", target]).copy()
    d = d[d["price"] > 0]
    X = pd.DataFrame({
        "minute": d["minute_of_day"].astype(float),
        "log_price": np.log(d["price"].astype(float)),
        "rsi": d["rsi"].astype(float),
        "spread": d["ema_spread"].fillna(0.0).astype(float),
        "dist_hod": d["dist_hod"].fillna(0.0).astype(float),
        "gap": d["gap"].fillna(0.0).astype(float),
        "pvt_above": d["pvt_above"].fillna(0.0).astype(float),
        "psar_up": d["psar_up"].fillna(0.0).astype(float),
        "log_float": np.log(d["free_float"].fillna(1.0e9).clip(lower=1.0e5).astype(float)),
        "dil": d["dilution"].fillna(0.0).astype(float),
        "log_vol": np.log1p(d["bar_volume"].fillna(0.0).astype(float)),
        "cost": d["cost_rt"].astype(float),
    }, index=d.index)
    y = (d[target] > 0).astype(int)
    return X, y, d["session_date"]


def learned_filter_walkforward(
    outcomes: pd.DataFrame,
    *,
    folds: int = 3,
    target: str = "net30",
) -> pd.DataFrame:
    """Expanding walk-forward of the meta-filters over sessions (plan §9, §10).

    Train uses the first 50% of sessions and grows; each of ``folds`` test blocks
    is the next slice of the remaining sessions — so models are NEVER trained on a
    test session. Per fold & model: ROC AUC, mean net over all test signals, and the
    top-decile n / mean net / win. Deterministic (fixed model seeds, sorted sessions).
    """
    cols = ["fold", "model", "train_sessions", "test_sessions", "n_train", "n_test",
            "auc", "all_net", "top_n", "top_net", "top_win"]
    if folds < 1:
        raise ValueError("folds must be >= 1")
    X, y, sess = _wfo_design(outcomes, target)
    dates = sorted(pd.unique(sess))
    n = len(dates)
    if n < 2:
        return pd.DataFrame(columns=cols)
    start = max(1, int(n * 0.5))
    edges = [start] + [start + round((n - start) * (k + 1) / folds) for k in range(folds)]
    net_col = outcomes.loc[X.index, target]

    rows: list[dict[str, Any]] = []
    for k in range(folds):
        tr_dates, te_dates = dates[:edges[k]], dates[edges[k]:edges[k + 1]]
        if not te_dates or edges[k] == 0:
            continue
        tr_mask = sess.isin(tr_dates)
        te_mask = sess.isin(te_dates)
        Xtr, ytr = X[tr_mask.values], y[tr_mask.values]
        Xte, yte = X[te_mask.values], y[te_mask.values]
        net_te = net_col[te_mask.values]
        label = {"fold": k,
                 "train_sessions": f"{tr_dates[0]}..{tr_dates[-1]}",
                 "test_sessions": f"{te_dates[0]}..{te_dates[-1]}",
                 "n_train": int(tr_mask.sum()), "n_test": int(te_mask.sum())}
        trainable = ytr.nunique() >= 2 and int((ytr == 0).sum()) >= 3 and int((ytr == 1).sum()) >= 3
        for name, cls in (("logistic", LogisticMetaFilter), ("gbm", GBMMetaFilter)):
            row = dict(label, model=name, auc=float("nan"), all_net=float("nan"),
                       top_n=0, top_net=float("nan"), top_win=float("nan"))
            if trainable and len(Xte) > 0:
                model = cls()
                model.fit(Xtr, ytr)
                p = np.array([model.score(dict(zip(X.columns, r))) for r in Xte.to_numpy()])
                row["auc"] = (float(roc_auc_score(yte, p)) if yte.nunique() > 1 else float("nan"))
                row["all_net"] = float(net_te.mean())
                thr = float(np.quantile(p, 0.9))
                top = net_te[p >= thr]
                row["top_n"] = int(len(top))
                row["top_net"] = float(top.mean()) if len(top) else float("nan")
                row["top_win"] = float((top > 0).mean()) if len(top) else float("nan")
            rows.append(row)
    return pd.DataFrame(rows, columns=cols)
