"""Score the pre-registered ETF allocation test once (docs/findings/prereg_etf_allocation_2026-10-29.md).

Usage:
    python scripts/etf_allocation_study.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from sgt.allocation.backtest import run
from sgt.allocation.metrics import summarize
from sgt.allocation.signals import LOOKBACK, dual_momentum_top3, fixed, gtaa_trend, tsmom_vol_scaled

RISK = ["SPY", "IWM", "EFA", "EEM", "VNQ", "IEF", "TLT", "GLD", "DBC"]
CASH = "SHY"
END = "2026-10-16"
PERIODS = {"full": None, "post-publication": "2013-01-01", "held-out": "2024-10-01"}
CACHE = Path("data/etf_yf/adj_close.parquet")


def load_yf() -> pd.DataFrame:
    if CACHE.exists():
        return pd.read_parquet(CACHE)
    import yfinance as yf

    raw = yf.download(RISK + [CASH], start="1990-01-01", end="2026-10-17", auto_adjust=True,
                      progress=False, threads=False)["Close"]
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    raw.to_parquet(CACHE)
    return raw


def integrity(yf_px: pd.DataFrame) -> dict[str, float]:
    out = {}
    for s in RISK + [CASH]:
        p = Path("data/daily") / f"{s}.parquet"
        a = pd.read_parquet(p).set_index("date")["close"]
        a.index = pd.to_datetime(a.index)
        both = pd.concat([yf_px[s], a], axis=1, keys=["yf", "alpaca"]).dropna()
        out[s] = float(both.pct_change().dropna().corr().iloc[0, 1])
    return out


def main() -> int:
    px = load_yf()
    corr = integrity(px)
    bad = [s for s, c in corr.items() if not c >= 0.99]
    print("integrity (daily-return corr vs Alpaca):", {k: round(v, 4) for k, v in corr.items()})
    if bad:
        print("EXCLUDED (corr < 0.99):", bad)
    risk = [s for s in RISK if s not in bad]
    px = px[risk + [CASH]].loc[:END].dropna(how="any")
    start = px.index[LOOKBACK + 21]          # every lookback available
    print(f"common history {px.index[0].date()} .. {px.index[-1].date()}; trading from {start.date()}")

    strategies = {
        "S1 TSMOM vol-scaled (PRIMARY)": tsmom_vol_scaled,
        "S2 GTAA 10m SMA": gtaa_trend,
        "S3 dual momentum top-3": dual_momentum_top3,
        "B1 SPY buy&hold": fixed({"SPY": 1.0}),
        "B2 60/40 SPY/IEF": fixed({"SPY": 0.6, "IEF": 0.4}),
        "B3 equal-weight 9": fixed({s: 1 / len(risk) for s in risk}),
    }
    cash_r = px[CASH].pct_change().fillna(0.0)
    results = {k: run(px, CASH, f, start=start) for k, f in strategies.items()}
    table = {}
    for pname, pstart in PERIODS.items():
        print(f"\n## {pname}" + (f" (from {pstart})" if pstart else f" (from {start.date()})"))
        print(f"{'strategy':32s} {'CAGR':>7s} {'vol':>6s} {'Sharpe':>7s} {'maxDD':>7s} {'worstYr':>8s} {'total':>8s} {'turn/yr':>8s}")
        for k, res in results.items():
            r = res.returns if pstart is None else res.returns.loc[pstart:]
            to = res.turnover if pstart is None else res.turnover.loc[pstart:]
            m = summarize(r, cash_r, to)
            table[(pname, k)] = m
            print(f"{k:32s} {100 * m['CAGR']:6.2f}% {100 * m['vol']:5.1f}% {m['sharpe']:7.2f} "
                  f"{100 * m['max_dd']:6.1f}% {100 * m['worst_year']:7.1f}% {100 * m['total']:7.1f}% "
                  f"{m.get('turnover_yr', 0):8.2f}")
        cr = cash_r.loc[pstart or start:]
        print(f"{'cash (SHY)':32s} total {100 * ((1 + cr).prod() - 1):.1f}%")

    s1, b3 = "S1 TSMOM vol-scaled (PRIMARY)", "B3 equal-weight 9"
    pp, ho = table[("post-publication", s1)], table[("post-publication", b3)]
    held = table[("held-out", s1)]
    cash_ho = (1 + cash_r.loc[PERIODS["held-out"]:]).prod() - 1
    ok = (pp["sharpe"] > ho["sharpe"]) and (pp["max_dd"] > ho["max_dd"]) and (held["total"] > cash_ho)
    print(f"\nPRIMARY checks: Sharpe {pp['sharpe']:.2f} vs B3 {ho['sharpe']:.2f}; maxDD "
          f"{100 * pp['max_dd']:.1f}% vs B3 {100 * ho['max_dd']:.1f}%; held-out total "
          f"{100 * held['total']:.1f}% vs cash {100 * cash_ho:.1f}%")
    print("VERDICT:", "PASS" if ok else "FAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
