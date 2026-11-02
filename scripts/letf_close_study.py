"""Leveraged-ETF rebalancing -> last-half-hour continuation, scored ONCE.

Rules: docs/findings/prereg_letf_close_2026-11-01.md.

Usage:
    python scripts/letf_close_study.py --dry-run     # trade counts only
    python scripts/letf_close_study.py
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from aaoi_study import LAST, OPEN, side_cost
from sgt.intraday.store import ParquetBarStore
from telegram_alert_test import boot_means, top5_share

LAUNCH = {"TSLA": date(2022, 8, 9), "NVDA": date(2022, 12, 13), "COIN": date(2022, 8, 9),
          "AAPL": date(2022, 8, 9), "AMZN": date(2022, 9, 7), "GOOGL": date(2022, 9, 7),
          "MSFT": date(2022, 9, 7), "AMD": date(2024, 3, 18), "META": date(2024, 6, 5),
          "SMCI": date(2024, 8, 22), "AVGO": date(2024, 8, 22), "MSTR": date(2024, 9, 18),
          "NFLX": date(2024, 10, 3), "MU": date(2024, 10, 10), "PLTR": date(2024, 12, 11),
          "AAOI": date(2026, 3, 24)}
LETFS = {"TSLA": "TSLL,TSLT,TSLQ,TSLZ", "NVDA": "NVDL,NVDX,NVDU", "COIN": "CONL", "AAPL": "AAPU",
         "AMZN": "AMZU", "GOOGL": "GGLL", "MSFT": "MSFU", "AMD": "AMDL", "META": "METU", "SMCI": "SMCX,SMCL",
         "AVGO": "AVL,AVGX", "MSTR": "MSTU,MSTZ", "NFLX": "NFXL", "MU": "MUU", "PLTR": "PLTU", "AAOI": "AAOX,AAOZ"}
START, END = date(2016, 1, 4), date(2026, 10, 30)
M1530, POST_LAG, SD_N = 930, 20, 20
SEED, ALPHA = 20261102, 0.025
OUT = Path("reports/letf_close")


def sessions(f: pd.DataFrame) -> pd.DataFrame:
    """One row per session: 15:30 open, 15:59 close, full-session flag."""
    f = f[(f.segment == "rth") & (f.minute_of_day >= OPEN) & (f.minute_of_day <= LAST)]
    day = pd.to_datetime(f.session_date).dt.date
    g = f.groupby(day)
    s = pd.DataFrame({"first": g.open.first(), "close": g.close.last(), "last_min": g.minute_of_day.max()})
    s["p1530"] = f[f.minute_of_day == M1530].groupby(day[f.minute_of_day == M1530]).open.first()
    s["c1559"] = f[f.minute_of_day == LAST].groupby(day[f.minute_of_day == LAST]).close.last()
    return s.sort_index()


def trades(s: pd.DataFrame, launch: date) -> pd.DataFrame:
    """Prereg rule on a session table; returns trades with period 'pre' / 'post' (None = skipped gap)."""
    s = s.copy()
    s["prev"] = s.c1559.shift(1)
    cc = s.c1559 / s.prev - 1
    s["sd20"] = cc.rolling(SD_N).std().shift(1)
    split = ~(s["first"] / s.prev).between(0.4, 2.5) & s.prev.notna()
    s["clean"] = ~split.rolling(SD_N + 1, min_periods=1).max().astype(bool)
    s["r"] = s.p1530 / s.prev - 1
    ok = (s.last_min == LAST) & s.clean & s[["p1530", "c1559", "prev", "sd20"]].notna().all(axis=1)
    t = s[ok & (s.r.abs() >= s.sd20)].copy()
    t["side"] = np.sign(t.r)
    t["gross"] = t.side * (t.c1559 / t.p1530 - 1)
    t["net"] = t.gross - [side_cost(a) + side_cost(b) for a, b in zip(t.p1530, t.c1559)]
    days = list(s.index)
    post_start = days[min(days.index(min(d for d in days if d >= launch)) + POST_LAG, len(days) - 1)] \
        if any(d >= launch for d in days) else None
    t["period"] = np.where(t.index < launch, "pre", np.where(post_start and t.index >= post_start, "post", None))
    return t[t.period.notna()]


def test_stats(t: pd.DataFrame, rng: np.random.Generator, col: str = "net") -> dict:
    t = t.sort_index()
    x = t[col].to_numpy(float)
    if len(x) == 0:
        return {"n": 0}
    lb = float(np.quantile(boot_means(x, np.array([str(d) for d in t.index]), rng), ALPHA))
    h = len(x) // 2
    return {"n": len(x), "mean": float(x.mean()), "win": float((x > 0).mean()), "lower": lb,
            "halves": (float(x[:h].mean()), float(x[h:].mean())), "top5": top5_share(x)}


def diff_lower(post: pd.DataFrame, pre: pd.DataFrame, rng: np.random.Generator) -> tuple[float, float]:
    a = boot_means(post.gross.to_numpy(float), np.array([str(d) for d in post.index]), rng)
    b = boot_means(pre.gross.to_numpy(float), np.array([str(d) for d in pre.index]), rng)
    return float(post.gross.mean() - pre.gross.mean()), float(np.quantile(a - b, ALPHA))


def letf_intensity(store_daily: dict[str, pd.DataFrame], sym: str) -> pd.Series:
    """Sum of the stock's 2x funds' 20-day dollar volume / the stock's 20-day dollar volume, lagged."""
    dv = lambda d: (d.vw * d.v).rolling(20).mean().shift(1)          # noqa: E731
    funds = [dv(store_daily[x]) for x in LETFS[sym].split(",") if x in store_daily]
    if not funds:
        return pd.Series(dtype=float)
    return pd.concat(funds, axis=1).sum(axis=1, min_count=1) / dv(store_daily[sym])


def daily_bars(symbols: list[str]) -> dict[str, pd.DataFrame]:
    import httpx

    from sgt.core.secrets import get_secret

    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    out: dict[str, list] = {}
    tok = None
    with httpx.Client(headers=h, timeout=30) as c:
        while True:
            p = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": "2022-01-01", "end": str(END),
                 "limit": 10000, "adjustment": "raw", "feed": "sip"}
            if tok:
                p["page_token"] = tok
            r = c.get("https://data.alpaca.markets/v2/stocks/bars", params=p)
            r.raise_for_status()
            j = r.json()
            for s, b in (j.get("bars") or {}).items():
                out.setdefault(s, []).extend(b)
            tok = j.get("next_page_token")
            if not tok:
                break
    return {s: pd.DataFrame(b).assign(day=lambda d: pd.to_datetime(d.t).dt.date).set_index("day") for s, b in out.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    store = ParquetBarStore("data/alpaca")
    all_t = []
    for sym, launch in LAUNCH.items():
        t = trades(sessions(store.read_frame(sym, START, END)), launch).assign(sym=sym)
        print(f"{sym:5s} launch {launch}: pre {int((t.period == 'pre').sum()):4d}  post {int((t.period == 'post').sum()):4d}")
        all_t.append(t)
    t = pd.concat(all_t)
    if args.dry_run:
        return 0
    post, pre = t[t.period == "post"], t[t.period == "pre"]
    rng = np.random.default_rng(SEED)
    l1 = test_stats(post, rng)
    l1["pass"] = bool(l1["n"] >= 300 and l1["mean"] > 0 and l1["lower"] > 0 and min(l1["halves"]) > 0 and l1["top5"] < 0.5)
    d, dlb = diff_lower(post, pre, rng)
    l2 = {"post_gross": float(post.gross.mean()), "pre_gross": float(pre.gross.mean()), "diff": d, "lower": dlb,
          "pass": bool(dlb > 0)}
    daily = daily_bars(sorted(set(LAUNCH) | {x for v in LETFS.values() for x in v.split(",")}))
    sec = {"per_stock": {}, "official_close_exit": None, "costs_2bp": None, "intensity_terciles": None}
    for sym, g in t.groupby("sym"):
        sec["per_stock"][sym] = {p: {"n": int(len(x)), "gross": float(x.gross.mean()), "net": float(x.net.mean())}
                                 for p, x in g.groupby("period")}
    po = post.copy()
    po["off_close"] = [daily[s].c.get(d, np.nan) if s in daily else np.nan for s, d in zip(po.sym, po.index)]
    oc = po.side * (po.off_close / po.p1530 - 1) - [side_cost(a) + side_cost(b) for a, b in zip(po.p1530, po.off_close)]
    sec["official_close_exit"] = {"n": int(oc.notna().sum()), "net": float(oc.mean())}
    sec["costs_2bp"] = float((post.gross - 2 * 0.0002 - 2 * 0.0035 / post.p1530).mean())
    po["intensity"] = [letf_intensity(daily, s).get(d, np.nan) for s, d in zip(po.sym, po.index)]
    q = po.dropna(subset=["intensity"])
    if len(q) >= 30:
        q = q.assign(terc=pd.qcut(q.intensity, 3, labels=["low", "mid", "high"]))
        sec["intensity_terciles"] = {str(k): {"n": int(len(g)), "gross": float(g.gross.mean()), "net": float(g.net.mean()),
                                              "median_intensity": float(g.intensity.median())}
                                     for k, g in q.groupby("terc", observed=True)}
    res = {"L1_post_tradeable": l1, "L2_mechanism": l2, "secondary": sec}
    print(f"\nL1 post-launch pooled: n={l1['n']} mean net {l1['mean']:+.3%} win {l1['win']:.0%} lower {l1['lower']:+.3%} "
          f"halves {l1['halves'][0]:+.3%}/{l1['halves'][1]:+.3%} top5 {l1['top5']:.2f} -> {'PASS' if l1['pass'] else 'FAIL'}")
    print(f"L2 mechanism: gross post {l2['post_gross']:+.3%} vs pre {l2['pre_gross']:+.3%}, diff {d:+.3%}, lower {dlb:+.3%} "
          f"-> {'PASS' if l2['pass'] else 'FAIL'}")
    print("secondary:", json.dumps(sec, indent=1, default=lambda v: round(v, 5)))
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "trades.csv")
    (OUT / "result.json").write_text(json.dumps(res, indent=2, default=str))
    from sgt.db.session import get_session, init_db
    from sgt.decision import trial_ledger

    init_db()
    with get_session() as s:
        for k in ("L1_post_tradeable", "L2_mechanism"):
            trial_ledger.record_trial(s, run_id="letf-close-2026-11-01", kind="letf-close",
                                      params={"prereg": "docs/findings/prereg_letf_close_2026-11-01.md", "test": k},
                                      metrics=json.loads(json.dumps({**res[k], "secondary": sec}, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
