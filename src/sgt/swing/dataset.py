"""Swing decision dataset from daily bars (A, 2026-10-18).

Emits the SAME column contract as the intraday decision dataset (symbol, ts, session_date,
trigger, f_* features, cost_rt_* columns, y_net__/y_exit_bar__/uniq_w__ per label config),
so ``scripts/train_decision_model.py`` -- nested CPCV, cost stress, shuffle null, DSR,
lockbox -- runs unchanged. ``meta.json`` records ``label_unit: "days"`` and the cost spec.

Universe (point-in-time at the signal close): price >= ``price_min`` and 20-day average
dollar volume >= ``dollar_vol_min``. Cross-sectional features rank a candidate's 5- and
20-day returns against every universe stock on the same date (all known at that close).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.decision.cost_spec import spec_of
from sgt.decision.features import CostModelAdapter, CostSource
from sgt.decision.labels import uniqueness_weights
from sgt.swing.features import FEATURES, market_features, symbol_features
from sgt.swing.labels import SwingLabel, bracket_labels, default_swing_grid

log = logging.getLogger(__name__)
XS_FEATURES = ("f_xs_ret5_pct", "f_xs_ret20_pct")


def _load(path: Path) -> pd.DataFrame | None:
    df = pd.read_parquet(path)
    if df.empty or len(df) < 300:
        return None
    return df.sort_values("date").reset_index(drop=True)


def build_swing_dataset(*, daily_dir: str | Path, out_dir: str | Path, dataset_id: str,
                        cost_source: CostSource, price_min: float = 5.0,
                        dollar_vol_min: float = 5e6, market_symbol: str = "SPY",
                        grid: Sequence[SwingLabel] | None = None) -> dict:
    daily_dir, grid = Path(daily_dir), tuple(grid or default_swing_grid())
    spy = _load(daily_dir / f"{market_symbol}.parquet")
    if spy is None:
        raise SystemExit(f"market proxy {market_symbol} missing from {daily_dir}")
    mkt = market_features(spy)
    model_src = CostModelAdapter(DEFAULT_COST_MODEL)

    universe_parts, cand_parts = [], []
    for path in sorted(daily_dir.glob("*.parquet")):
        sym = path.stem.upper()
        df = _load(path)
        if df is None:
            continue
        f = symbol_features(df).join(df[["date", "open", "high", "low", "close"]])
        f = f.merge(mkt, on="date", how="left")
        elig = (f["close"] >= price_min) & (f["_dollar_vol_20"] >= dollar_vol_min)
        universe_parts.append(pd.DataFrame({"symbol": sym, "date": f.loc[elig, "date"],
                                            "r5": f.loc[elig, "f_ret_5"].astype("float32"),
                                            "r20": f.loc[elig, "f_ret_20"].astype("float32")}))
        cand = np.flatnonzero((elig & f["trigger"].notna()).to_numpy())
        if cand.size == 0:
            continue
        o, h, l, c = (f[k].to_numpy(float) for k in ("open", "high", "low", "close"))
        atr = f["_atr"].to_numpy(float)
        rt = np.array([cost_source.round_trip(sym, None, float(c[i]))[0] for i in cand])  # type: ignore[arg-type]
        rt_model = np.array([model_src.round_trip(sym, None, float(c[i]))[0] for i in cand])  # type: ignore[arg-type]
        rows = f.iloc[cand][["date", "trigger", *FEATURES]].copy()
        rows.insert(0, "symbol", sym)
        rows["cost_rt_label"], rows["cost_rt_model"] = rt, rt_model
        for cfg in grid:
            net, held = bracket_labels(o, h, l, c, atr, cand, cfg, rt / 2.0)
            spans = [(int(i) + 1, int(i) + int(k)) for i, k in zip(cand, held) if k == k]
            w = iter(uniqueness_weights(spans, len(c)))
            rows[f"y_net__{cfg.suffix}"] = net
            rows[f"y_exit_bar__{cfg.suffix}"] = held
            rows[f"uniq_w__{cfg.suffix}"] = [next(w) if k == k else np.nan for k in held]
        cand_parts.append(rows)
        log.info("swing-dataset: %s -> %d candidates", sym, len(rows))

    if not cand_parts:
        raise SystemExit("no candidates")
    data = pd.concat(cand_parts, ignore_index=True)
    uni = pd.concat(universe_parts, ignore_index=True)
    uni["f_xs_ret5_pct"] = uni.groupby("date")["r5"].rank(pct=True)
    uni["f_xs_ret20_pct"] = uni.groupby("date")["r20"].rank(pct=True)
    data = data.merge(uni[["symbol", "date", *XS_FEATURES]], on=["symbol", "date"], how="left")
    data["session_date"] = data.pop("date").astype(str)
    data["ts"] = pd.to_datetime(data["session_date"]).dt.tz_localize("America/New_York") \
        + pd.Timedelta(hours=16)
    data["ts"] = data["ts"].dt.tz_convert("UTC")
    data["cost_rt_measured"] = np.nan
    data["cost_source"] = getattr(cost_source, "tag", "model")
    data["discovered_session"] = ""
    data["cluster_key"] = data["session_date"]   # dates, not (symbol, date), are independent
    data = data.sort_values(["ts", "symbol"]).reset_index(drop=True)

    out = Path(out_dir) / dataset_id
    out.mkdir(parents=True, exist_ok=True)
    data.to_parquet(out / "dataset.parquet", index=False)
    sessions = sorted(data["session_date"].unique().tolist())
    meta = {
        "dataset_id": dataset_id, "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_store": str(daily_dir), "rows": int(len(data)),
        "symbols": int(data["symbol"].nunique()), "sessions": sessions,
        "data_range": [sessions[0], sessions[-1]],
        "feature_names": [*FEATURES, *XS_FEATURES], "label_unit": "days",
        "label_grid": [{"k_tp": g.k_tp, "k_sl": g.k_sl, "h": g.h, "suffix": g.suffix} for g in grid],
        "universe": f"daily_liquid(price>={price_min},dv20>={dollar_vol_min:.0f})",
        "cost_model": type(cost_source).__name__, "cost_spec": spec_of(cost_source),
        "research_ungated": False, "promotion_eligible": True,
        "bias_flags": ["delisting_returns_missing"],
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
    return meta
