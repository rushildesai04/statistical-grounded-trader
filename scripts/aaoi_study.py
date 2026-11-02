"""AAOI + high-beta peers: intraday rules (opening-range breakout, VWAP trend) and time-of-day profile.

Rules: docs/findings/prereg_aaoi_intraday_2026-11-01.md. Training (2016-2023) is for exploration;
the held-out period (2024-01-02 .. 2026-10-30) is scored once by ``score`` after the prereg commit.

Usage:
    python scripts/aaoi_study.py explore            # TRAINING ONLY: profiles + rule results
    python scripts/aaoi_study.py score --dry-run    # held-out counts only
    python scripts/aaoi_study.py score              # ONE-SHOT held-out verdict
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.intraday.store import ParquetBarStore

PEERS = ("AAOI", "LITE", "COHR", "CIEN", "FN", "SMCI", "MRVL", "CRDO")
TRAIN = (date(2016, 1, 4), date(2023, 12, 29))
HOLDOUT = (date(2024, 1, 2), date(2026, 10, 30))
OPEN, OR_END, LAST = 570, 575, 959          # minute_of_day ET: 09:30, 09:35, 15:59 bar
COMMISSION = 0.0035                          # $/share/side (the ORB paper's IBKR figure)
SLIP = 0.0005                                # 5 bp/side spread + slippage (our assumption)
ATR_N, RV_N, STOP_ATR = 14, 14, 0.10
OUT = Path("reports/aaoi")


def rth_frame(store: ParquetBarStore, sym: str, start: date, end: date) -> pd.DataFrame:
    f = store.read_frame(sym, start, end)
    if f.empty:
        return f
    f = f[(f.segment == "rth") & (f.minute_of_day >= OPEN) & (f.minute_of_day <= LAST)].copy()
    f["session_date"] = pd.to_datetime(f.session_date).dt.date
    return f.sort_values("ts").reset_index(drop=True)


def daily_from_minutes(f: pd.DataFrame) -> pd.DataFrame:
    """RTH daily OHLCV + 5-minute opening range, ATR14 / avg volume / relative OR volume known at
    the open (all shifted one day: no look-ahead)."""
    g = f.groupby("session_date")
    d = pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last(),
                      "volume": g.volume.sum(), "full": g.minute_of_day.max() == LAST})
    o = f[f.minute_of_day < OR_END].groupby("session_date")
    d["or_open"], d["or_high"], d["or_low"] = o.open.first(), o.high.max(), o.low.min()
    d["or_close"], d["or_vol"] = o.close.last(), o.volume.sum()
    pc = d.close.shift(1)
    tr = pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)
    split = (d.open / pc).between(0.4, 2.5).eq(False) & pc.notna()           # raw prices: skip splits
    d["atr"] = tr.rolling(ATR_N).mean().shift(1)
    d["avg_vol"] = d.volume.rolling(ATR_N).mean().shift(1)
    d["rv"] = d.or_vol / d.or_vol.rolling(RV_N).mean().shift(1)
    d["clean"] = ~split.rolling(ATR_N + 1, min_periods=1).max().astype(bool)
    return d


def side_cost(px: float) -> float:
    """Fractional one-side cost at price ``px``."""
    return SLIP + COMMISSION / px


def orb_trades(f: pd.DataFrame, d: pd.DataFrame, rv_min: float = 1.0) -> pd.DataFrame:
    """5-minute ORB on stocks in play (Zarattini, Barbon & Aziz 2024): direction of the first 5-minute
    candle; stop entry at its high/low; stop loss 10% ATR14 from entry; exit 15:59 close.
    Filters: open > $5, avg vol14 >= 1M, ATR14 > $0.50, relative OR volume >= ``rv_min``.
    Intrabar ambiguity is resolved against us (entry and stop in one bar = stopped)."""
    rows = []
    by_day = {k: v for k, v in f.groupby("session_date")}
    for day, r in d.iterrows():
        if not (r.full and r.clean) or np.isnan(r.atr) or r.or_close == r.or_open:
            continue
        if not (r.open > 5 and r.avg_vol >= 1_000_000 and r.atr > 0.5 and r.rv >= rv_min):
            continue
        side = 1 if r.or_close > r.or_open else -1
        lvl = r.or_high if side > 0 else r.or_low
        b = by_day[day]
        b = b[b.minute_of_day >= OR_END]
        trig = b[(b.high >= lvl) if side > 0 else (b.low <= lvl)]
        if trig.empty:
            continue
        i = trig.index[0]
        entry = float(max(lvl, b.open.loc[i]) if side > 0 else min(lvl, b.open.loc[i]))
        risk = STOP_ATR * r.atr
        stop = entry - side * risk
        after = b.loc[i:]
        hit = after[(after.low <= stop) if side > 0 else (after.high >= stop)]
        if len(hit):
            j = hit.index[0]
            o = b.open.loc[j]
            exit_ = float(stop if j == i else (min(stop, o) if side > 0 else max(stop, o)))
        else:
            exit_ = float(b.close.iloc[-1])
        gross = side * (exit_ - entry) / entry
        net = gross - side_cost(entry) - side_cost(exit_)
        rows.append({"day": day, "side": side, "entry": entry, "exit": exit_, "rv": r.rv, "risk_pct": risk / entry,
                     "gross": gross, "net": net, "R": net / (risk / entry), "stopped": bool(len(hit)),
                     "entry_min": int(b.minute_of_day.loc[i]),
                     "exit_min": int(b.minute_of_day.loc[hit.index[0]]) if len(hit) else LAST})
    return pd.DataFrame(rows)


def vwap_trades(f: pd.DataFrame, start_min: int = OR_END, every: int = 1) -> pd.DataFrame:
    """VWAP trend: from 09:35, hold +1 while the close is above the session VWAP and -1 while below,
    re-checked every ``every`` minutes (09:34, then each ``every`` minutes); flips at the next bar's
    open; flat at 15:59 close. One row per session."""
    rows = []
    for day, b in f.groupby("session_date"):
        if b.minute_of_day.max() != LAST:
            continue
        tp = (b.high + b.low + b.close) / 3
        vw = (tp * b.volume).cumsum() / b.volume.cumsum().replace(0, np.nan)
        check = (b.minute_of_day >= start_min - 1) & ((b.minute_of_day - (start_min - 1)) % every == 0)
        sig = np.sign(b.close - vw).where(check).ffill().where(b.minute_of_day >= start_min - 1).fillna(0).to_numpy()
        pos = np.r_[0, sig[:-1]]                                    # act on the next bar's open
        o, c = b.open.to_numpy(), b.close.to_numpy()
        # bar k's position earns open->close of k; the k-1 close -> k open gap belongs to bar k-1's
        # position (any flip happens at k's open)
        ret = pos * (c / o - 1) + np.r_[0, pos[:-1] * (o[1:] / c[:-1] - 1)]
        turns = np.abs(np.diff(np.r_[0, pos, 0])).sum()             # units traded incl. final flatten
        cost = turns * side_cost(float(b.close.mean()))
        rows.append({"day": day, "gross": float(ret.sum()), "net": float(ret.sum() - cost), "turns": float(turns)})
    return pd.DataFrame(rows)


def tod_profile(f: pd.DataFrame) -> pd.DataFrame:
    """Per 30-minute slot: mean return, share of up slots, volatility, share of daily volume."""
    f = f.assign(slot=((f.minute_of_day - OPEN) // 30).clip(upper=12))
    g = f.groupby(["session_date", "slot"])
    s = pd.DataFrame({"ret": g.close.last() / g.open.first() - 1, "vol": g.volume.sum()})
    s["vshare"] = s.vol / s.groupby(level=0).vol.transform("sum")
    p = s.groupby(level=1).agg(mean=("ret", "mean"), up=("ret", lambda x: (x > 0).mean()),
                               sd=("ret", "std"), vshare=("vshare", "mean"))
    p.index = [f"{(OPEN + 30 * k) // 60:02d}:{(OPEN + 30 * k) % 60:02d}" for k in p.index]
    return p


def summarize(x: pd.Series) -> dict:
    x = x.dropna()
    sd = x.std(ddof=1)
    return {"n": int(len(x)), "mean": float(x.mean()) if len(x) else None, "win": float((x > 0).mean()) if len(x) else None,
            "sharpe_ann": float(x.mean() / sd * np.sqrt(252)) if len(x) > 2 and sd > 0 else None}


def explore() -> int:
    store = ParquetBarStore("data/alpaca")
    out = {}
    for s in PEERS:
        f = rth_frame(store, s, *TRAIN)
        if f.empty:
            print(f"{s}: no training bars")
            continue
        d = daily_from_minutes(f)
        o, v = orb_trades(f, d), vwap_trades(f)
        o0 = orb_trades(f, d, rv_min=0.0)
        out[s] = {"days": len(d), "orb": summarize(o.R) if len(o) else None,
                  "orb_net_pct": summarize(o.net) if len(o) else None,
                  "orb_all_rv": summarize(o0.R) if len(o0) else None, "vwap": summarize(v.net)}
        print(f"\n{s} training days {len(d)}")
        if len(o):
            print(f"  ORB RV>=1 trades {len(o)}  mean R {o.R.mean():+.3f}  net/trade {o.net.mean():+.3%}  "
                  f"win {(o.net > 0).mean():.0%}  stopped {o.stopped.mean():.0%}  longs {int((o.side > 0).sum())}")
        if len(o0):
            print(f"  ORB any RV  trades {len(o0)}  mean R {o0.R.mean():+.3f}  net/trade {o0.net.mean():+.3%}")
        print(f"  VWAP trend  days {len(v)}  gross/day {v.gross.mean():+.3%}  net/day {v.net.mean():+.3%}  "
              f"turns/day {v.turns.mean():.1f}")
        if s == "AAOI":
            print(tod_profile(f).to_string(float_format=lambda x: f"{x:+.3%}"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "explore_training.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


def verdict(t: pd.DataFrame, rng: np.random.Generator) -> dict:
    """Prereg statistic: mean net/trade, session-clustered 2.5% lower bound, halves, top-5 share."""
    from telegram_alert_test import boot_means, top5_share

    t = t.sort_values(["day", "sym"])
    x = t.net.to_numpy(float)
    if len(x) == 0:
        return {"n": 0, "pass": False}
    lb = float(np.quantile(boot_means(x, t.day.astype(str).to_numpy(), rng), 0.025))
    h = len(t) // 2
    halves = (float(t.net.iloc[:h].mean()), float(t.net.iloc[h:].mean()))
    t5 = top5_share(x)
    ok = len(x) >= 100 and lb > 0 and min(halves) > 0 and t5 < 0.5
    return {"n": len(x), "mean": float(x.mean()), "median": float(np.median(x)), "win": float((x > 0).mean()),
            "lower": lb, "halves": halves, "top5": t5, "mean_R": float(t.R.mean()), "gross": float(t.gross.mean()),
            "pass": bool(ok)}


def etf_vehicle(t: pd.DataFrame, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Secondary: AAOI signals traded through AAOX (long) / AAOZ (short) at the same minutes."""
    rows = []
    for r in t[t.sym == "AAOI"].itertuples():
        f = frames.get("AAOX" if r.side > 0 else "AAOZ")
        if f is None:
            continue
        b = f[f.session_date == r.day].set_index("minute_of_day")
        if r.entry_min not in b.index or (r.exit_min not in b.index and r.exit_min != LAST) or LAST not in b.index:
            continue
        e = float(b.open.loc[r.entry_min])
        x = float(b.close.loc[LAST]) if r.exit_min == LAST else float(b.open.loc[r.exit_min])
        rows.append({"day": r.day, "net": x / e - 1 - side_cost(e) - side_cost(x)})
    return pd.DataFrame(rows)


def score(dry_run: bool) -> int:
    store = ParquetBarStore("data/alpaca")
    trades, frames = [], {}
    for s in PEERS:
        f = rth_frame(store, s, date(2023, 11, 1), HOLDOUT[1])        # 14-day lookbacks need late 2023
        d = daily_from_minutes(f)
        d = d[(d.index >= HOLDOUT[0]) & (d.index <= HOLDOUT[1])]
        o = orb_trades(f, d)
        print(f"{s}: held-out sessions {len(d)}, trades {len(o)}")
        trades.append(o.assign(sym=s))
    t = pd.concat(trades, ignore_index=True)
    if dry_run:
        return 0
    for s in ("AAOX", "AAOZ"):
        frames[s] = rth_frame(store, s, HOLDOUT[0], HOLDOUT[1])
    rng = np.random.default_rng(20261101)
    res = {"H1_pooled": verdict(t, rng), "H2_AAOI": verdict(t[t.sym == "AAOI"], rng)}
    cost2 = t.net - (t.gross - t.net)
    res["secondary"] = {
        "long": float(t[t.side > 0].net.mean()), "short": float(t[t.side < 0].net.mean()),
        "rv2": float(t[t.rv >= 2].net.mean()), "rv2_n": int((t.rv >= 2).sum()), "costs_x2": float(cost2.mean()),
        "per_name": {s: {"n": int(len(g)), "net": float(g.net.mean()), "R": float(g.R.mean())} for s, g in t.groupby("sym")},
        "aaoi_by_year": {str(y): float(g.net.mean()) for y, g in t[t.sym == "AAOI"].groupby(pd.to_datetime(t[t.sym == "AAOI"].day).dt.year)}}
    v = etf_vehicle(t, frames)
    res["secondary"]["aaoi_via_aaox_aaoz"] = {"n": int(len(v)), "net": float(v.net.mean()) if len(v) else None}
    for k in ("H1_pooled", "H2_AAOI"):
        r = res[k]
        print(f"\n{k}: n={r['n']} mean net {r['mean']:+.3%} (gross {r['gross']:+.3%}, R {r['mean_R']:+.3f}) median "
              f"{r['median']:+.3%} win {r['win']:.0%} lower {r['lower']:+.3%} halves {r['halves'][0]:+.3%}/"
              f"{r['halves'][1]:+.3%} top5 {r['top5']:.2f} -> {'PASS' if r['pass'] else 'FAIL'}")
    print("\nsecondary:", json.dumps(res["secondary"], indent=1, default=lambda v: round(v, 5)))
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "holdout_trades.csv", index=False)
    (OUT / "result.json").write_text(json.dumps(res, indent=2, default=str))
    from sgt.db.session import get_session, init_db
    from sgt.decision import trial_ledger

    init_db()
    with get_session() as s:
        for k in ("H1_pooled", "H2_AAOI"):
            trial_ledger.record_trial(s, run_id="aaoi-intraday-2026-11-01", kind="aaoi-intraday",
                                      params={"prereg": "docs/findings/prereg_aaoi_intraday_2026-11-01.md", "test": k},
                                      metrics=json.loads(json.dumps({**res[k], "secondary": res["secondary"]}, default=str)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["explore", "score"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return explore() if args.cmd == "explore" else score(args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
