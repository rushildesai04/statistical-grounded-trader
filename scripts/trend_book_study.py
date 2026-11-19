"""The trader's trend matrix as a position rule on a stock in play (docs/findings/prereg_trend_book_2026-11-18.md).

    .venv/bin/python scripts/trend_book_study.py              # training, run ONCE
    .venv/bin/python scripts/trend_book_study.py --holdout    # only a rule that passed on training, once

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_feed_replay_study import events  # noqa: E402
from long_setups_study import latest  # noqa: E402
from trend_matrix_exit_study import NAMES, matrix  # noqa: E402

from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/trend_book")
RULES = ("TREND", "TREND2")
CAREFUL_COST = 0.002
ALPHA, SEED, N_BOOT = 0.05, 20261118, 10_000
ET = "America/New_York"
PRICE = (("$1-2", 0, 2), ("$2-5", 2, 5), ("$5-10", 5, 10), ("above $10", 10, np.inf))
TIMES = (("09:30-10:30", 570, 630), ("10:30-13:00", 630, 780), ("13:00-16:00", 780, 960))


def _naive(ts) -> np.ndarray:
    """Timestamps as timezone-free UTC ``datetime64[ns]`` (whatever they came in as)."""
    idx = pd.DatetimeIndex(ts)
    return (idx.tz_convert("UTC").tz_localize(None) if idx.tz is not None else idx).to_numpy()


def two_minute(day: pd.DataFrame) -> pd.DataFrame:
    """2-minute candles from 1-minute ones (clock-aligned), each with the time from which it is complete."""
    t = pd.Series(_naive(day.ts))
    b = day.reset_index(drop=True).assign(bucket=t.dt.floor("2min").to_numpy())
    g = b.groupby("bucket", sort=True)
    out = pd.DataFrame({"ts": g.bucket.first().to_numpy(), "open": g.open.first().to_numpy(), "high": g.high.max().to_numpy(),
                        "low": g.low.min().to_numpy(), "close": g.close.last().to_numpy(), "volume": g.volume.sum().to_numpy()})
    out["done"] = out.ts.to_numpy() + np.timedelta64(2, "m")
    return out


def columns(day: pd.DataFrame, rth_ts) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """At each regular-hours 1-minute close: is the 1M column all green, is MACD green, is the 2M column all green
    (from 2-minute candles COMPLETED by that close)."""
    m1 = matrix(day).set_index("ts").reindex(rth_ts)[list(NAMES)].fillna(False).to_numpy(bool)
    two = two_minute(day)
    m2 = matrix(two)[list(NAMES)].to_numpy(bool).all(axis=1)
    done = two.done.to_numpy()
    closes = _naive(rth_ts) + np.timedelta64(1, "m")                              # a 1-minute candle closes here
    k = np.searchsorted(done, closes, side="right") - 1                          # last 2-minute candle complete by then
    all2 = np.where(k >= 0, m2[np.clip(k, 0, len(m2) - 1)], False) if len(m2) else np.zeros(len(closes), bool)
    return m1.all(axis=1), m1[:, 0], all2


def round_trips(o: np.ndarray, c: np.ndarray, enter: np.ndarray, macd: np.ndarray, start: int, last_entry: int) -> list[dict]:
    """The rule from candle ``start`` on: flat and ``enter`` at a close -> buy the next open (no entry at or after
    candle ``last_entry``); in and MACD red at a close -> sell the next open; in at the end -> sell the last close."""
    n, out, pos = len(c), [], None
    for j in range(start, n):
        if pos is None:
            if enter[j] and j + 1 < n and j + 1 < last_entry:
                pos = j + 1
        elif j >= pos and not macd[j]:
            if j + 1 < n:
                out.append({"in_i": pos, "out_i": j + 1, "entry": o[pos], "exit": o[j + 1]})
                pos = None
    if pos is not None:
        out.append({"in_i": pos, "out_i": n - 1, "entry": o[pos], "exit": c[n - 1]})
    return [t for t in out if t["entry"] > 0]


def one_event(e, day: pd.DataFrame) -> list[dict]:
    day = day.assign(ts=pd.to_datetime(day.ts, utc=True)).sort_values("ts").reset_index(drop=True)
    et = day.ts.dt.tz_convert(ET)
    mins = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    day = day[mins < 16 * 60].reset_index(drop=True)
    rth = day[day.segment == "rth"].reset_index(drop=True)
    t0 = pd.Timestamp(e.t0)
    if not len(rth) or not (rth.ts == t0).any():
        return []
    ts = rth.ts.to_numpy()
    all1, macd, all2 = columns(day, ts)
    start = int(np.flatnonzero((rth.ts == t0).to_numpy())[0])
    cut = latest(e.session)
    last_entry = int((rth.ts < cut).sum())                                       # candles before the last entry minute's end
    o, c = rth.open.to_numpy(float), rth.close.to_numpy(float)
    rmin = (rth.ts.dt.tz_convert(ET).dt.hour * 60 + rth.ts.dt.tz_convert(ET).dt.minute).to_numpy()
    rows = []
    for rule, enter in (("TREND", all1), ("TREND2", all1 & all2)):
        for k, t in enumerate(round_trips(o, c, enter, macd, start, last_entry)):
            g = t["exit"] / t["entry"] - 1
            rows.append({"rule": rule, "symbol": e.symbol, "session": e.session, "k": k, "entry": t["entry"], "gross": g,
                         "net": g - float(e.cost_rt), "cost": float(e.cost_rt), "minutes": int(t["out_i"] - t["in_i"]),
                         "minute": int(rmin[t["in_i"]])})
    return rows


def _boot(sess: np.ndarray, val: np.ndarray) -> np.ndarray | None:
    u, inv = np.unique(sess, return_inverse=True)
    if len(u) < 2 or not len(val):
        return None
    s, n = np.bincount(inv, val, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    den = n[b].sum(1)
    return s[b].sum(1)[den > 0] / den[den > 0]


def describe(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"round_trips": 0}
    x = t.net.to_numpy(float)
    return {"round_trips": int(len(t)), "gross": float(t.gross.mean()), "net": float(x.mean()), "win": float((x > 0).mean()),
            "net_at_careful_cost": float((t.gross - CAREFUL_COST).mean()), "median_minutes": float(t.minutes.median()),
            "mean_cost": float(t.cost.mean())}


def evaluate(t: pd.DataFrame, events_n: int, only: list[str] | None = None) -> dict:
    tested = list(only) if only is not None else list(RULES)
    alpha = ALPHA / max(1, len(tested))
    res: dict = {"events": int(events_n), "alpha_each": alpha, "rules": {}, "passes": []}
    for rule in RULES:
        g = t[t.rule == rule]
        d = describe(g)
        if len(g):
            per_day = g.groupby(["symbol", "session"]).agg(net=("net", "sum"), gross=("gross", "sum"), n=("net", "size"))
            x = np.sort(g.net.to_numpy(float))
            k = max(1, len(x) // 100)
            d.update(stock_days=int(len(per_day)), share_of_events_traded=float(len(per_day) / events_n) if events_n else None,
                     round_trips_per_stock_day=float(per_day.n.mean()), per_stock_day_net=float(per_day.net.mean()),
                     per_stock_day_gross=float(per_day.gross.mean()), per_stock_day_win=float((per_day.net > 0).mean()),
                     best_1pct_mean=float(x[-k:].mean()), worst_1pct_mean=float(x[:k].mean()),
                     first_round_trip=describe(g[g.k == 0]), later_round_trips=describe(g[g.k > 0]),
                     by_time={n: describe(g[(g.minute >= a) & (g.minute < b)]) for n, a, b in TIMES},
                     by_price={n: describe(g[(g.entry >= a) & (g.entry < b)]) for n, a, b in PRICE})
            if rule in tested:
                m = _boot(g.session.to_numpy(), g.net.to_numpy(float))
                d["lower"] = float(np.quantile(m, alpha)) if m is not None and len(m) else None
                d["pass"] = bool(d["lower"] is not None and d["lower"] > 0)
                mg = _boot(g.session.to_numpy(), g.gross.to_numpy(float))
                d["gross_lo95"], d["gross_hi95"] = (float(np.quantile(mg, 0.025)), float(np.quantile(mg, 0.975))) if mg is not None else (None, None)
                if d["pass"]:
                    res["passes"].append(rule)
        res["rules"][rule] = d
    return res


def collect(split: str) -> tuple[pd.DataFrame, int]:
    store, rows, n = ParquetBarStore("data/alpaca"), [], 0
    for e in events(split).itertuples(index=False):
        d = date.fromisoformat(e.session)
        f = store.read_frame(e.symbol, d, d)
        if not len(f):
            continue
        n += 1
        rows += one_event(e, f)
    return pd.DataFrame(rows), n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--holdout", action="store_true", help="the held-out events, once, only for a rule that passed on training")
    a = ap.parse_args(argv)
    split = "holdout" if a.holdout else "train"
    res_path = a.out / f"result_{split}.json"
    if res_path.is_file():
        print(f"{res_path} exists: the {split} run was made once and is not repeated")
        return 2
    only = None
    if a.holdout:
        train = a.out / "result_train.json"
        only = json.loads(train.read_text()).get("passes") if train.is_file() else None
        if not only:
            print("the held-out events are opened only for a rule that passed on training")
            return 2
    t, n = collect(split)
    if t.empty:
        raise SystemExit("no round trips: nothing reported")
    res = {"prereg": "docs/findings/prereg_trend_book_2026-11-18.md", "split": split, **evaluate(t, n, only)}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1, default=str) + "\n")
    t.to_csv(a.out / f"{split}_round_trips.csv", index=False)
    print(json.dumps(res, indent=1, default=str)[:3500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
