"""Forward paper test: F1 dual momentum, F2 60/40 + trend filter, vs B 60/40.

Rules: docs/findings/prereg_forward_paper_2026-10-29.md. Virtual sleeves, no orders.

Usage:
    python scripts/forward_paper.py refresh     # download prices + Alpaca cross-check
    python scripts/forward_paper.py snapshot    # record this month's target weights (append-only)
    python scripts/forward_paper.py evaluate    # NAV / return / drawdown vs B, decision status
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.allocation.backtest import run as backtest
from sgt.allocation.signals import dual_momentum_top3, fixed, month_ends, sixty_forty_trend

CASH = "SHY"
F1_UNIVERSE = ["SPY", "IWM", "EFA", "EEM", "IEF", "TLT", "GLD", "DBC"]
SYMBOLS = F1_UNIVERSE + [CASH]
FIRST_SIGNAL = pd.Timestamp("2026-10-30")
DECISION_AFTER = pd.Timestamp("2027-10-29")
EARLY_ABANDON_DD_GAP = 0.10
COST_BP = 5.0
NAV0 = 100_000.0
MIN_CORR = 0.99
PRICES = Path("data/etf_yf/adj_close_forward.parquet")
OUT = Path("reports/forward")
SLEEVES = {  # name -> (universe incl. cash, strategy)
    "F1_dual_momentum": (SYMBOLS, dual_momentum_top3),
    "F2_6040_trend": (["SPY", "IEF", CASH], sixty_forty_trend),
    "B_6040": (["SPY", "IEF", CASH], fixed({"SPY": 0.6, "IEF": 0.4})),
}


def load_prices() -> pd.DataFrame:
    if not PRICES.exists():
        raise SystemExit(f"{PRICES} missing; run `refresh` first")
    return pd.read_parquet(PRICES)


def refresh() -> int:
    import yfinance as yf

    raw = yf.download(SYMBOLS, start="2000-01-01", auto_adjust=True, progress=False, threads=False,
                      timeout=30)["Close"]
    if raw is None or raw.empty or set(SYMBOLS) - set(raw.columns):
        raise SystemExit("yfinance returned no/partial data; prices NOT updated")
    raw.index = pd.to_datetime(raw.index).tz_localize(None)
    raw = raw[SYMBOLS].dropna(how="all")
    PRICES.parent.mkdir(parents=True, exist_ok=True)
    raw.to_parquet(PRICES)
    print(f"prices: {raw.index[0].date()} .. {raw.index[-1].date()} -> {PRICES}")
    bad = cross_check(raw)
    return 1 if bad else 0


def _alpaca_daily(symbol: str, start: date) -> pd.Series:
    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars"
    params = {"symbols": symbol, "timeframe": "1Day", "start": start.isoformat(), "adjustment": "all",
              "feed": "sip", "limit": 10000}
    r = httpx.get(url, params=params, headers=_alpaca_headers(), timeout=20.0)
    r.raise_for_status()
    bars = (r.json().get("bars") or {}).get(symbol) or []
    s = pd.Series({pd.Timestamp(b["t"]).tz_convert("America/New_York").tz_localize(None).normalize(): float(b["c"])
                   for b in bars}, dtype=float)
    return s.sort_index()


def cross_check(px: pd.DataFrame) -> list[str]:
    """Daily-return correlation yfinance vs Alpaca over the forward window (>= 15 days needed)."""
    start = FIRST_SIGNAL.date()
    bad = []
    for s in SYMBOLS:
        a = _alpaca_daily(s, start)
        both = pd.concat([px[s], a], axis=1, keys=["yf", "alpaca"]).loc[FIRST_SIGNAL:].dropna()
        rets = both.pct_change().dropna()
        if len(rets) < 15:
            print(f"  {s}: cross-check n/a ({len(rets)} overlapping days)")
            continue
        c = float(rets.corr().iloc[0, 1])
        flag = "OK" if c >= MIN_CORR else "HALT"
        print(f"  {s}: corr {c:.4f} {flag}")
        if c < MIN_CORR:
            bad.append(s)
    if bad:
        print(f"HALT: {bad} disagree with Alpaca (< {MIN_CORR}); evaluation must not be reported")
    return bad


def target_weights(px: pd.DataFrame, signal: pd.Timestamp) -> dict[str, dict[str, float]]:
    out = {}
    for name, (universe, strat) in SLEEVES.items():
        w = strat(px[universe].loc[:signal].dropna(how="any"), CASH, signal).clip(lower=0.0)
        w[CASH] = max(0.0, 1.0 - float(w.sum()))
        out[name] = {k: round(float(v), 10) for k, v in w.items() if v > 0}
    return out


def completed_signals(px: pd.DataFrame) -> list[pd.Timestamp]:
    """Month-end closes >= FIRST_SIGNAL whose month is complete (a later trading day exists)."""
    idx = px.dropna(how="any").index
    return [t for t in month_ends(idx) if t >= FIRST_SIGNAL and (idx > t).any() and idx[idx > t][0].month != t.month]


def snapshot() -> int:
    px = load_prices()
    sigs = completed_signals(px)
    if not sigs:
        print("no completed month-end signal yet (first is 2026-10-30)")
        return 0
    OUT.mkdir(parents=True, exist_ok=True)
    for t in sigs:
        path = OUT / f"{(t + pd.offsets.MonthBegin(1)):%Y-%m}.json"
        if path.exists():
            continue  # append-only
        rec = {"signal_date": str(t.date()), "written_at": datetime.now(timezone.utc).isoformat(),
               "weights": target_weights(px, t)}
        path.write_text(json.dumps(rec, indent=2))
        print(f"wrote {path}: {rec['weights']}")
    return 0


def _verify_snapshots(px: pd.DataFrame) -> list[str]:
    problems = []
    for path in sorted(OUT.glob("????-??.json")):
        rec = json.loads(path.read_text())
        now = target_weights(px, pd.Timestamp(rec["signal_date"]))
        for name, w in rec["weights"].items():
            keys = set(w) | set(now.get(name, {}))
            if any(abs(w.get(k, 0.0) - now.get(name, {}).get(k, 0.0)) > 1e-6 for k in keys):
                problems.append(f"{path.name} {name}: recorded {w} != recomputed {now.get(name)}")
    return problems


def sleeve_curves(px: pd.DataFrame) -> pd.DataFrame:
    curves = {}
    for name, (universe, strat) in SLEEVES.items():
        res = backtest(px[universe], CASH, strat, start=FIRST_SIGNAL, cost_bp=COST_BP)
        r = res.returns
        if len(res.weights):
            r = r.loc[res.weights.index[0]:]  # forward window starts at the first execution
        curves[name] = NAV0 * (1.0 + r).cumprod()
    return pd.DataFrame(curves)


def evaluate() -> int:
    px = load_prices()
    problems = _verify_snapshots(px)
    for p in problems:
        print("SNAPSHOT MISMATCH:", p)
    nav = sleeve_curves(px).dropna()
    if len(nav) < 2:
        print("forward window has not started (first execution 2026-11-02)")
        return 1 if problems else 0
    dd = nav / nav.cummax() - 1.0
    month_end_dd = dd.groupby([dd.index.year, dd.index.month]).tail(1)
    print(f"forward window {nav.index[0].date()} .. {nav.index[-1].date()} ({len(nav)} days)")
    print(f"{'sleeve':18s} {'NAV':>12s} {'total':>8s} {'max_dd':>8s}")
    for c in nav:
        print(f"{c:18s} {nav[c].iloc[-1]:12,.0f} {nav[c].iloc[-1] / NAV0 - 1:8.2%} {dd[c].min():8.2%}")
    final = nav.index[-1] >= DECISION_AFTER
    b_tot, b_dd = nav["B_6040"].iloc[-1] / NAV0 - 1, dd["B_6040"].min()
    for c in ("F1_dual_momentum", "F2_6040_trend"):
        tot, mdd = nav[c].iloc[-1] / NAV0 - 1, dd[c].min()
        abandon = bool((month_end_dd[c] < month_end_dd["B_6040"] - EARLY_ABANDON_DD_GAP).any())
        ok = tot >= b_tot and mdd >= b_dd
        status = ("ABANDONED (drawdown > B + 10pp)" if abandon else
                  ("PASS" if ok else "FAIL") if final else f"interim: {'ahead' if ok else 'behind'} of B")
        print(f"{c}: {status}")
    return 1 if problems else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["refresh", "snapshot", "evaluate"])
    args = ap.parse_args(argv)
    return {"refresh": refresh, "snapshot": snapshot, "evaluate": evaluate}[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
