"""Pre-registered post-publication anomaly tests (intraday momentum, overnight, turn of month), ONCE.

Rules: docs/findings/prereg_anomalies_2026-10-31.md.

Usage:
    python scripts/anomalies_study.py --dry-run     # sample counts only
    python scripts/anomalies_study.py
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from megacap_study import adjusted_daily
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.calendar import default_calendar
from sgt.intraday.store import ParquetBarStore

ET = ZoneInfo("America/New_York")
COST = 0.0002                     # 2 bp per round trip
ALPHA = 0.05 / 3                  # one family of three tests
N_BOOT, SEED = 10_000, 20261031
OUT = Path("reports/anomalies")
M_1000, M_1530, M_1559 = 599, 930, 959      # minute_of_day (ET) of the 09:59, 15:30, 15:59 bars


def lower(x: np.ndarray, rng: np.random.Generator, alpha: float = ALPHA) -> float:
    idx = rng.integers(0, len(x), size=(N_BOOT, len(x)))
    return float(np.quantile(x[idx].mean(axis=1), alpha))


def halves(x: pd.Series) -> tuple[float, float]:
    h = len(x) // 2
    return float(x.iloc[:h].mean()), float(x.iloc[h:].mean())


# ------------------------------------------------------------------ A1 intraday momentum
def intraday_momentum(f: pd.DataFrame, cost: float = COST) -> pd.DataFrame:
    """Per session: r1 (prev 15:59 close -> 09:59 close), last-half-hour return (15:30 open -> 15:59 close),
    position sign(r1), net. Early-close sessions and sessions missing a needed bar are dropped."""
    cal = default_calendar()
    rth = (f[f.segment == "rth"] if "segment" in f else f).copy()
    rth["session_date"] = pd.to_datetime(rth.session_date).dt.date      # store returns ISO strings
    piv = {k: rth[rth.minute_of_day == m].set_index("session_date")[col]
           for k, m, col in (("p1000", M_1000, "close"), ("e1530", M_1530, "open"), ("x1559", M_1559, "close"))}
    df = pd.DataFrame(piv).sort_index()
    df["prev"] = df.x1559.shift(1)
    full = [d for d in df.index if cal.bounds(d).rth_close.astimezone(ET).hour == 16]
    df = df.loc[full].dropna()
    df["r1"] = df.p1000 / df.prev - 1
    df["r_last"] = df.x1559 / df.e1530 - 1
    df["position"] = np.sign(df.r1)
    df["net"] = df.position * df.r_last - cost * (df.position != 0)
    return df


# ------------------------------------------------------------------ A2 overnight
def overnight(d: pd.DataFrame, cost: float = COST) -> pd.DataFrame:
    """Long close t -> open t+1 on adjusted daily bars; row dated t."""
    d = d.sort_index()
    ret = d.open.shift(-1) / d.close - 1
    out = pd.DataFrame({"ret": ret, "net": ret - cost, "hold": d.close.shift(-1) / d.close - 1}).dropna()
    return out


# ------------------------------------------------------------------ A3 turn of month
def turn_of_month(px: pd.Series, cost: float = COST) -> tuple[pd.DataFrame, pd.Series]:
    """Windows: close of the 2nd-to-last session of month M -> close of the 3rd session of M+1.
    Returns (per-window frame dated by exit, daily-return flag 'in window')."""
    px = px.dropna().sort_index()
    idx = px.index
    months = pd.Series(idx.to_period("M"), index=idx)
    rows, inside = [], pd.Series(False, index=idx)
    pos = {p: np.flatnonzero((months == p).to_numpy()) for p in months.unique()}
    periods = sorted(pos)
    for m, nxt in zip(periods[:-1], periods[1:]):
        a, b = pos[m], pos[nxt]
        if len(a) < 2 or len(b) < 3:
            continue
        i_in, i_out = a[-2], b[2]
        r = px.iloc[i_out] / px.iloc[i_in] - 1
        rows.append({"exit": idx[i_out], "ret": r, "net": r - cost})
        inside.iloc[i_in + 1:i_out + 1] = True        # daily returns earned while held
    return pd.DataFrame(rows).set_index("exit"), inside


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    store = ParquetBarStore("data/alpaca")
    s1, e1 = date(2018, 6, 29), date(2026, 10, 29)      # prev close needed for 2018-07-02
    im = {s: intraday_momentum(store.read_frame(s, s1, e1)) for s in ("SPY", "QQQ")}
    im = {s: v[v.index >= date(2018, 7, 2)] for s, v in im.items()}
    on = overnight(adjusted_daily("SPY"))
    on = on[(on.index >= date(2016, 1, 5)) & (on.index <= date(2026, 10, 28))]   # open of 10-29 = last
    yf = pd.read_parquet("data/etf_yf/adj_close_forward.parquet")["SPY"]
    yf = yf[yf.index <= "2026-10-29"]
    tom, inside = turn_of_month(yf)
    tom = tom[(tom.index >= "2000-02-01") & (tom.index <= "2026-10-31")]
    print(f"A1 SPY days {len(im['SPY'])} ({im['SPY'].index.min()} .. {im['SPY'].index.max()}), QQQ {len(im['QQQ'])}")
    print(f"A2 nights {len(on)} ({on.index.min()} .. {on.index.max()})")
    print(f"A3 windows {len(tom)} ({tom.index.min().date()} .. {tom.index.max().date()})")
    if args.dry_run:
        return 0

    rng = np.random.default_rng(SEED)
    res = {}
    a = im["SPY"]
    two_x = (a.net - COST * (a.position != 0)).mean()
    h = halves(a.net)
    res["A1_intraday_momentum"] = {"n": len(a), "mean": float(a.net.mean()), "lower": lower(a.net.to_numpy(), rng),
                                   "mean_2x": float(two_x), "halves": h, "hit": float((a.net > 0).mean()),
                                   "ann": float(a.net.mean() * 252), "qqq_mean": float(im["QQQ"].net.mean())}
    r = res["A1_intraday_momentum"]
    r["pass"] = r["lower"] > 0 and r["mean_2x"] > 0 and min(h) > 0

    h = halves(on.net)
    days = len(on)
    bh = on.hold
    res["A2_overnight"] = {"n": days, "mean": float(on.net.mean()), "lower": lower(on.net.to_numpy(), rng),
                           "mean_2x": float((on.net - COST).mean()), "halves": h,
                           "ann_net": float(on.net.mean() * 252),
                           "sharpe_net": float(on.net.mean() / on.net.std() * np.sqrt(252)),
                           "bh_ann": float(bh.mean() * 252), "bh_sharpe": float(bh.mean() / bh.std() * np.sqrt(252)),
                           "gross_ann": float(on.ret.mean() * 252)}
    r = res["A2_overnight"]
    r["pass"] = r["lower"] > 0 and r["mean_2x"] > 0 and min(h) > 0

    dr = yf.pct_change()
    m_in, m_out = float(dr[inside].mean()), float(dr[~inside].iloc[1:].mean())
    h = halves(tom.net)
    res["A3_turn_of_month"] = {"n": len(tom), "mean": float(tom.net.mean()), "lower": lower(tom.net.to_numpy(), rng),
                               "halves": h, "hit": float((tom.net > 0).mean()), "daily_in": m_in, "daily_out": m_out,
                               "ann_net": float(tom.net.mean() * 12)}
    r = res["A3_turn_of_month"]
    r["pass"] = r["lower"] > 0 and min(h) > 0 and m_in > m_out

    for k, v in res.items():
        print(f"\n{k}: {'PASS' if v['pass'] else 'FAIL'}")
        print("  " + "  ".join(f"{kk}={vv:+.4%}" if isinstance(vv, float) and kk not in ("hit",) and "sharpe" not in kk
                               else f"{kk}={vv}" for kk, vv in v.items() if kk != "pass"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(res, indent=2, default=str))
    init_db()
    with get_session() as s:
        for k, v in res.items():
            trial_ledger.record_trial(s, run_id="anomalies-2026-10-31", kind="anomaly",
                                      params={"prereg": "docs/findings/prereg_anomalies_2026-10-31.md", "test": k},
                                      metrics=json.loads(json.dumps(v, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
