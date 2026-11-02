"""Institutional flow ("wash" = VWAP, FINRA short volume, 13F breadth) -> learned entry/exit/stop.

Rules: docs/findings/prereg_inst_flow_2026-11-02.md. Study id ``inst-flow-2026-11`` in the holdout
vault (config/holdout_registry.json): training 2016-01-04..2022-12-30, held-out 2023-01-03..2026-10-30.

Usage:
    python scripts/inst_flow_study.py panel            # build the point-in-time feature panel (cached)
    python scripts/inst_flow_study.py cv               # TRAINING ONLY: walk-forward rule search, ledger
    python scripts/inst_flow_study.py score --dry-run  # counts only, no returns
    python scripts/inst_flow_study.py score            # unlocks the vault ONCE, held-out verdict
"""
from __future__ import annotations

import argparse
import itertools
import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

STUDY = "inst-flow-2026-11"
RUN_ID = "inst-flow-2026-11-02"
OUT = Path("reports/inst_flow")
PANEL = OUT / "panel.parquet"
SEED, ALPHA, N_BOOT, BLOCK = 20261102, 0.025, 10_000, 20
MIN_PRICE, MIN_DV, MIN_HIST = 5.0, 20e6, 252
COST_SIDE, BORROW_YR = 0.0010, 0.01
VWAP = ["vwap_gap_1", "vwap_gap_5", "vwap_above_20", "clv_20", "relvol_5"]
FINRA = ["sv_ratio_1", "sv_ratio_5", "sv_z_60", "offex_share_5"]
F13 = ["breadth_chg", "net_new_share", "inst_shares_chg"]
BASE = ["beta_252", "vol_20", "atr_pct", "mom_252_21", "rev_5", "ln_dv_20", "ret_1"]
FLOW = VWAP + FINRA + F13
GBM = dict(objective="regression", n_estimators=200, learning_rate=0.05, num_leaves=31, min_child_samples=500,
           colsample_bytree=0.8, subsample=0.8, subsample_freq=1, random_state=SEED, verbose=-1)
VAL_YEARS = (2019, 2020, 2021, 2022)


@dataclass(frozen=True)
class Rule:
    h: int            # holding sessions (enter next open, exit close of the h-th session)
    q: float          # long top q / short bottom q of the cross-section by prediction
    stop: float       # stop at stop x ATR% from entry (0 = none)
    side: str         # "ls" long-short, "lh" long top q hedged with SPY by beta


GRID = [Rule(h, q, s, side) for h, q, s, side in
        itertools.product((1, 5, 10, 20), (0.05, 0.10), (0.0, 2.0, 3.0), ("ls", "lh"))]


# ------------------------------------------------------------------ panel (features are as-of the close of t)
def load_daily(min_rows: int = MIN_HIST) -> pd.DataFrame:
    """Daily bars (adjusted, active + delisted) for symbols that were ever liquid enough to qualify
    (90th-percentile dollar volume >= MIN_DV and >= MIN_HIST sessions), plus SPY. The per-date
    eligibility filter is applied later; this only bounds memory."""
    import duckdb
    import pyarrow.parquet as pq

    files = [str(f) for f in sorted(Path("data/daily").glob("*.parquet")) if pq.read_metadata(f).num_rows > 0]
    q = f"""WITH d AS (SELECT regexp_extract(filename, '([^/]+)\\.parquet$', 1) AS symbol, date, open, high, low,
                       close, volume, vwap FROM read_parquet({files!r}, filename=true)),
            s AS (SELECT symbol FROM d GROUP BY symbol HAVING (count(*) >= {min_rows}
                  AND quantile_cont(close * volume, 0.9) >= {MIN_DV} AND max(close) >= {MIN_PRICE}) OR symbol = 'SPY')
            SELECT d.* FROM d JOIN s USING (symbol)"""
    d = duckdb.sql(q).df()
    d["date"] = pd.to_datetime(d.date).astype("datetime64[ns]")
    return d.sort_values(["symbol", "date"]).reset_index(drop=True)


def price_features(d: pd.DataFrame, spy: pd.Series) -> pd.DataFrame:
    g = d.groupby("symbol", sort=False)
    ret = g.close.pct_change()
    d = d.assign(ret_1=ret)
    gap = d.close / d.vwap - 1
    rng = (d.high - d.low).replace(0, np.nan)
    clv = ((2 * d.close - d.high - d.low) / rng).fillna(0) * d.volume
    prev = g.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - prev).abs(), (d.low - prev).abs()], axis=1).max(axis=1)
    dv = d.close * d.volume
    roll = lambda s, w, f="mean": getattr(s.groupby(d.symbol, sort=False).rolling(w, min_periods=w), f)() \
        .reset_index(level=0, drop=True)                                                  # noqa: E731
    m = d.date.map(spy.pct_change())
    cov = roll(ret * m, 252) - roll(ret, 252) * roll(m, 252)
    out = pd.DataFrame({
        "symbol": d.symbol, "date": d.date, "open": d.open, "high": d.high, "low": d.low, "close": d.close,
        "ret_1": ret, "vwap_gap_1": gap, "vwap_gap_5": roll(gap, 5), "vwap_above_20": roll((gap > 0).astype(float), 20),
        "clv_20": roll(clv, 20, "sum") / roll(d.volume, 20, "sum"), "relvol_5": roll(d.volume, 5) / roll(d.volume, 60),
        "beta_252": 2 / 3 * cov / roll(m * m, 252).sub(roll(m, 252) ** 2) + 1 / 3,
        "vol_20": roll(ret, 20, "std") * np.sqrt(252), "atr_pct": roll(tr, 14) / d.close,
        "mom_252_21": g.close.shift(21) / g.close.shift(252) - 1, "rev_5": d.close / g.close.shift(5) - 1,
        "ln_dv_20": np.log(roll(dv, 20, "median").where(lambda x: x > 0)), "dv_20": roll(dv, 20, "median"), "volume": d.volume})
    return out


def finra_features(p: pd.DataFrame, path: Path = Path("data/finra/short_volume.parquet")) -> pd.DataFrame:
    """Short volume of day t is published after t's close: it is a feature of the close of t, used at t+1 open."""
    f = pd.read_parquet(path, columns=["date", "symbol", "short_vol", "total_vol"])
    f["date"] = pd.to_datetime(f.date).astype("datetime64[ns]")
    p = p.merge(f, on=["symbol", "date"], how="left")
    sv = p.short_vol / p.total_vol.where(p.total_vol > 0)
    g = sv.groupby(p.symbol, sort=False)
    sv5 = g.rolling(5, min_periods=3).mean().reset_index(level=0, drop=True)
    mu = sv5.groupby(p.symbol, sort=False).rolling(60, min_periods=40).mean().reset_index(level=0, drop=True)
    sd = sv5.groupby(p.symbol, sort=False).rolling(60, min_periods=40).std().reset_index(level=0, drop=True)
    off = (p.total_vol / p.volume.where(p.volume > 0)).groupby(p.symbol, sort=False) \
        .rolling(5, min_periods=3).mean().reset_index(level=0, drop=True)
    return p.assign(sv_ratio_1=sv, sv_ratio_5=sv5, sv_z_60=(sv5 - mu) / sd, offex_share_5=off.clip(upper=1.5)) \
        .drop(columns=["short_vol", "total_vol"])


def f13_features(p: pd.DataFrame, agg: Path = Path("data/sec13f/holdings_agg.parquet"),
                 cmap: Path = Path("data/sec13f/cusip_ticker.parquet")) -> pd.DataFrame:
    """Latest 13F period whose available_date <= t (as-of join; available = deadline + 1 session)."""
    h = pd.read_parquet(agg)
    m = pd.read_parquet(cmap)
    m = m[m.ticker.notna()][["cusip", "ticker"]].drop_duplicates("cusip")
    h = h.merge(m, on="cusip").rename(columns={"ticker": "symbol"})
    h = (h.groupby(["symbol", "period", "available_date"], as_index=False)
         [["n_holders", "total_shares", "n_new", "n_exit"]].sum().sort_values(["symbol", "period"]))
    g = h.groupby("symbol")
    h["breadth_chg"] = h.n_holders / g.n_holders.shift(1) - 1
    h["net_new_share"] = (h.n_new - h.n_exit) / g.n_holders.shift(1)
    h["inst_shares_chg"] = h.total_shares / g.total_shares.shift(1) - 1
    h["available_date"] = pd.to_datetime(h.available_date).astype("datetime64[ns]")
    p = p.assign(date=p.date.astype("datetime64[ns]"))
    h = h.sort_values("available_date")
    p = p.sort_values("date")
    out = pd.merge_asof(p, h[["symbol", "available_date"] + F13], left_on="date", right_on="available_date",
                        by="symbol", direction="backward")
    return out.drop(columns=["available_date"]).sort_values(["symbol", "date"]).reset_index(drop=True)


def build_panel() -> pd.DataFrame:
    d = load_daily()
    spy = d[d.symbol == "SPY"].set_index("date").close
    p = f13_features(finra_features(price_features(d, spy)))
    p["spy_close"] = p.date.map(spy)
    p["spy_open"] = p.date.map(d[d.symbol == "SPY"].set_index("date").open)
    p["eligible"] = (p.close >= MIN_PRICE) & (p.dv_20 >= MIN_DV) & p.beta_252.notna() & p.atr_pct.gt(0)
    OUT.mkdir(parents=True, exist_ok=True)
    p.to_parquet(PANEL)
    return p


# ------------------------------------------------------------------ targets + trades (path-dependent stops)
def forward_paths(p: pd.DataFrame, rows: np.ndarray, hmax: int = 20) -> dict[str, np.ndarray]:
    """For the rows in mask ``rows``: next-open entry and the next ``hmax`` sessions' open/high/low/close of
    the same symbol, plus SPY's next open and closes (float32; NaN past the end of ``p``)."""
    g = p.groupby("symbol", sort=False)
    f32 = lambda s: s.to_numpy(np.float32)[rows]                                          # noqa: E731
    out = {"entry": f32(g.open.shift(-1))}
    for k in range(1, hmax + 1):
        for col, key in (("open", "op"), ("high", "hi"), ("low", "lo"), ("close", "cl")):
            out[f"{key}{k}"] = f32(g[col].shift(-k))
    sg = p[["date", "spy_open", "spy_close"]].drop_duplicates("date").set_index("date").sort_index()
    out["spy_entry"] = f32(p.date.map(sg.spy_open.shift(-1)))
    for k in range(1, hmax + 1):
        out[f"spy{k}"] = f32(p.date.map(sg.spy_close.shift(-k)))
    return out


def trade_returns(paths: dict[str, np.ndarray], idx: np.ndarray, side: np.ndarray, atr: np.ndarray,
                  beta: np.ndarray, rule: Rule) -> np.ndarray:
    """Net, beta-hedged return per position. A stop breached intrabar fills at the stop level, or at
    that session's open when the open is already through it (gap); first breach wins."""
    e = paths["entry"][idx]
    exit_px = paths[f"cl{rule.h}"][idx].copy()
    if rule.stop > 0:
        hit = np.zeros(len(idx), bool)
        lvl = e * (1 - side * rule.stop * atr)
        for k in range(1, rule.h + 1):
            lo, hi, op = paths[f"lo{k}"][idx], paths[f"hi{k}"][idx], paths[f"op{k}"][idx]
            breach = ~hit & np.where(side > 0, lo <= lvl, hi >= lvl)
            fill = np.where(side > 0, np.minimum(lvl, op), np.maximum(lvl, op)) if k > 1 else lvl
            exit_px = np.where(breach, fill, exit_px)
            hit |= breach
    r = side * (exit_px / e - 1)
    spy = paths[f"spy{rule.h}"][idx] / paths["spy_entry"][idx] - 1
    hedge = -side * beta * spy if rule.side == "ls" else -beta * spy
    borrow = np.where(side < 0, BORROW_YR * rule.h / 252, 0.0)
    return r + hedge - 2 * COST_SIDE - borrow


def cohort_returns(p: pd.DataFrame, pred: np.ndarray, paths: dict, rule: Rule) -> pd.Series:
    """One cohort per signal date: top-q long (and bottom-q short for 'ls'), weights ~ 1/ATR%, gross 1;
    returned as return per holding day so horizons are comparable."""
    df = pd.DataFrame({"date": p.date.to_numpy(), "pred": pred, "atr": p.atr_pct.to_numpy(),
                       "beta": p.beta_252.to_numpy(), "row": np.arange(len(p))})
    rk = df.groupby("date").pred.rank(pct=True)
    longs = df[rk > 1 - rule.q].assign(side=1.0)
    legs = [longs] + ([df[rk <= rule.q].assign(side=-1.0)] if rule.side == "ls" else [])
    t = pd.concat(legs)
    t["r"] = trade_returns(paths, t.row.to_numpy(), t.side.to_numpy(), t.atr.to_numpy(), t.beta.to_numpy(), rule)
    t = t[np.isfinite(t.r)]
    t["w"] = 1 / t.atr
    t["w"] = t.w / t.groupby(["date", "side"]).w.transform("sum") / len(legs)
    return (t.w * t.r).groupby(t.date).sum() / rule.h


def block_lower(x: np.ndarray, rng: np.random.Generator, block: int = BLOCK) -> float:
    n = len(x)
    if n < 2 * block:
        return float("nan")
    starts = rng.integers(0, n - block + 1, size=(N_BOOT, int(np.ceil(n / block))))
    means = np.array([x[(s[:, None] + np.arange(block)).ravel()[:n]].mean() for s in starts])
    return float(np.quantile(means, ALPHA))


def stats(c: pd.Series) -> dict:
    x = c.to_numpy(float)
    h = len(x) // 2
    yearly = c.groupby(pd.DatetimeIndex(c.index).year).sum()
    return {"n_days": len(x), "mean": float(x.mean()), "sharpe": float(x.mean() / x.std(ddof=1) * np.sqrt(252)),
            "halves": (float(x[:h].mean()), float(x[h:].mean())),
            "max_year_share": float(yearly.clip(lower=0).max() / max(yearly.clip(lower=0).sum(), 1e-12))}


# ------------------------------------------------------------------ model
def fit(train: pd.DataFrame, feats: list[str], h: int):
    import lightgbm as lgb

    y = train[f"y{h}"]
    ok = y.notna()
    X = train.loc[ok, feats].groupby(train.loc[ok, "date"]).rank(pct=True)
    return lgb.LGBMRegressor(**GBM).fit(X, y[ok])


def predict(model, rows: pd.DataFrame, feats: list[str]) -> np.ndarray:
    return model.predict(rows[feats].groupby(rows.date).rank(pct=True))


def add_targets(p: pd.DataFrame, paths: dict) -> pd.DataFrame:
    """Learning target: beta-hedged next-open -> close of session h return, cross-sectionally ranked."""
    for h in (1, 5, 10, 20):
        r = paths[f"cl{h}"] / paths["entry"] - 1 - p.beta_252.to_numpy() * (paths[f"spy{h}"] / paths["spy_entry"] - 1)
        p[f"y{h}"] = pd.Series(r, index=p.index).groupby(p.date).rank(pct=True)
    return p


def train_view(vault, p: pd.DataFrame) -> pd.DataFrame:
    """Training rows only: forward paths are computed on this frame, so no target sees 2023+ prices."""
    return vault.train_rows(p, "date").reset_index(drop=True)


def eligible_with_paths(p: pd.DataFrame, rows: np.ndarray | None = None) -> tuple[pd.DataFrame, dict]:
    rows = p.eligible.to_numpy() if rows is None else rows
    paths = forward_paths(p, rows)
    return p[rows].reset_index(drop=True), paths


def cv(vault) -> pd.DataFrame:
    e, paths = eligible_with_paths(train_view(vault, pd.read_parquet(PANEL)))
    e = add_targets(e, paths)
    rows = []
    for feats_name, feats in (("full", BASE + FLOW), ("base", BASE)):
        for vy in VAL_YEARS:
            for h in (1, 5, 10, 20):
                tr = e[(e.date < pd.Timestamp(f"{vy}-01-01") - pd.offsets.BDay(h + 1))]   # purge overlapping targets
                model = fit(tr, feats, h)
                va_mask = (e.date.dt.year == vy).to_numpy()
                va = e[va_mask].reset_index(drop=True)
                pred = predict(model, va, feats)
                sub = {k: v[va_mask] for k, v in paths.items()}
                for rule in [r for r in GRID if r.h == h]:
                    c = cohort_returns(va, pred, sub, rule)
                    rows.append({"feats": feats_name, "val_year": vy, **asdict(rule), **stats(c)})
                print(f"cv {feats_name} {vy} h={h} done", flush=True)
    res = pd.DataFrame(rows)
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "cv.csv", index=False)
    return res


def select(res: pd.DataFrame, feats: str) -> Rule:
    s = res[res.feats == feats].groupby(["h", "q", "stop", "side"]).sharpe.mean().sort_values()
    h, q, stop, side = s.index[-1]
    return Rule(int(h), float(q), float(stop), str(side))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["panel", "cv", "score"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    from sgt.research.vault import Vault

    vault = Vault(STUDY)
    if args.cmd == "panel":
        p = build_panel()
        e = p[p.eligible]
        print(f"panel rows {len(p):,}; eligible {len(e):,}; symbols/day median "
              f"{e.groupby('date').size().median():.0f}; coverage FINRA {e.sv_ratio_1.notna().mean():.0%} "
              f"13F {e.breadth_chg.notna().mean():.0%}")
        return 0
    if args.cmd == "cv":
        res = cv(vault)
        from sgt.db.session import get_session, init_db
        from sgt.decision import trial_ledger

        init_db()
        with get_session() as s:
            for _, r in res.iterrows():
                trial_ledger.record_trial(s, run_id=RUN_ID + "-cv", kind="inst-flow-cv",
                                          params={k: r[k] for k in ("feats", "val_year", "h", "q", "stop", "side")},
                                          metrics={"sharpe": float(r.sharpe), "mean": float(r["mean"])})
        for f in ("full", "base"):
            print(f, "selected:", select(res, f))
        return 0
    return score(vault, args.dry_run)


def score(vault, dry_run: bool) -> int:
    res = pd.read_csv(OUT / "cv.csv")
    rules = {f: select(res, f) for f in ("full", "base")}
    p_all = pd.read_parquet(PANEL)
    if dry_run:
        ho = p_all[(p_all.date >= pd.Timestamp(vault.holdout_start)) & p_all.eligible]
        print(f"held-out eligible rows {len(ho):,}, dates {ho.date.nunique()}; rules {rules}")
        return 0
    vault.unlock("scripts/inst_flow_study.py")
    p_all = vault.frame(p_all[p_all.date <= pd.Timestamp(vault.study.holdout[1])], "date").reset_index(drop=True)
    train, tpaths = eligible_with_paths(train_view(vault, p_all))
    train = add_targets(train, tpaths)
    ho, sub = eligible_with_paths(p_all, ((p_all.date >= pd.Timestamp(vault.holdout_start)) & p_all.eligible).to_numpy())
    rng = np.random.default_rng(SEED)
    out, curves = {}, {}
    for f, feats in (("full", BASE + FLOW), ("base", BASE)):
        rule = rules[f]
        model = fit(train, feats, rule.h)
        curves[f] = cohort_returns(ho, predict(model, ho, feats), sub, rule)
        out[f] = {"rule": asdict(rule), **stats(curves[f]), "lower": block_lower(curves[f].to_numpy(), rng)}
    t1 = out["full"]
    t1["pass"] = bool(t1["lower"] > 0 and min(t1["halves"]) > 0 and t1["max_year_share"] < 0.5)
    both = pd.concat([curves["full"], curves["base"]], axis=1, join="inner").dropna()
    diff = both.iloc[:, 0] - both.iloc[:, 1]
    t2 = {"mean_diff": float(diff.mean()), "lower": block_lower(diff.to_numpy(), rng)}
    t2["pass"] = bool(t2["lower"] > 0)
    result = {"T1_tradeable": t1, "T2_flow_adds_value": t2, "base": out["base"], "vault_commit": vault.commit,
              "scored_on": str(date.today())}
    print(json.dumps(result, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(result, indent=2, default=str))
    pd.DataFrame(curves).to_csv(OUT / "heldout_cohorts.csv")
    from sgt.db.session import get_session, init_db
    from sgt.decision import trial_ledger

    init_db()
    with get_session() as s:
        for k in ("T1_tradeable", "T2_flow_adds_value"):
            trial_ledger.record_trial(s, run_id=RUN_ID, kind="inst-flow", params={"test": k, "prereg":
                                      "docs/findings/prereg_inst_flow_2026-11-02.md"},
                                      metrics=json.loads(json.dumps(result[k], default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
