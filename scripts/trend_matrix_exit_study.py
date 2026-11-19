"""The trader's trend matrix as the long book's exit (docs/findings/prereg_trend_matrix_exit_2026-11-18.md).

    .venv/bin/python scripts/trend_matrix_exit_study.py              # training, run ONCE
    .venv/bin/python scripts/trend_matrix_exit_study.py --holdout    # only an exit that passed on training, once

Report only: nothing here changes a book. The six conditions use the trader's settings; the code is written here from
the standard definitions of the indicators."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_feed_replay_study import events, pick, score, tape_setups  # noqa: E402
from long_setups_study import latest  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/trend_matrix_exit")
NAMES = ("macd", "ema", "rsi", "bb", "supertrend", "pvt")
MACD_FAST, MACD_SLOW, MACD_SIG, EMA_N, RSI_N, BB_N, ST_N, ST_MULT, PVT_N = 12, 31, 10, 34, 14, 20, 10, 3.0, 15
MIN_HISTORY = 60
EXITS = ("macd", "matrix")
ALPHA, SEED, N_BOOT = 0.05, 20261118, 10_000
ET = "America/New_York"


def supertrend_up(h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> np.ndarray:
    """True while the supertrend is up: bands at close -/+ ST_MULT x the average true range of ST_N candles; the
    lower band only rises while the close stays above it, the upper only falls while the close stays below it; the
    trend turns down on a close under the previous lower band and up on a close over the previous upper band."""
    n = len(c)
    prev = np.r_[c[0], c[:-1]]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    atr = pd.Series(tr).rolling(ST_N).mean().to_numpy()
    up, dn, trend = np.full(n, np.nan), np.full(n, np.nan), np.ones(n, int)
    for i in range(n):
        if not atr[i] == atr[i]:
            trend[i] = trend[i - 1] if i else 1
            continue
        u, d = c[i] - ST_MULT * atr[i], c[i] + ST_MULT * atr[i]
        u1, d1 = (up[i - 1], dn[i - 1]) if i and up[i - 1] == up[i - 1] else (u, d)
        up[i] = max(u, u1) if i and c[i - 1] > u1 else u
        dn[i] = min(d, d1) if i and c[i - 1] < d1 else d
        t = trend[i - 1] if i else 1
        trend[i] = 1 if (t == -1 and c[i] > d1) else -1 if (t == 1 and c[i] < u1) else t
    return trend == 1


def matrix(day: pd.DataFrame) -> pd.DataFrame:
    """The six 1-minute conditions (True = green) at each candle's close, over ``day`` (time-ordered candles)."""
    c, v = day.close.astype(float).reset_index(drop=True), day.volume.astype(float).reset_index(drop=True)
    ema = lambda x, n: x.ewm(span=n, adjust=False).mean()  # noqa: E731
    macd = ema(c, MACD_FAST) - ema(c, MACD_SLOW)
    d = c.diff()
    gain, loss = d.clip(lower=0).ewm(alpha=1 / RSI_N, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / RSI_N, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    pvt = (c.pct_change().fillna(0) * v).cumsum()
    out = pd.DataFrame({"macd": macd > ema(macd, MACD_SIG), "ema": c > ema(c, EMA_N), "rsi": rsi.fillna(100 if len(c) else 50) > 50,
                        "bb": c > c.rolling(BB_N, min_periods=1).mean(),
                        "supertrend": supertrend_up(day.high.to_numpy(float), day.low.to_numpy(float), c.to_numpy()),
                        "pvt": pvt > ema(pvt, PVT_N)})
    return out.assign(ts=day.ts.to_numpy())


def exit_on(rth: pd.DataFrame, red: np.ndarray, entry_i: int, entry: float, stop: float) -> dict:
    """Hold from the entry candle: the stop first (at the stop, or the open when a later candle opens below), then
    sell at the next open after the first close with ``red`` true; else the session's last close."""
    o, lo, c = rth.open.to_numpy(float), rth.low.to_numpy(float), rth.close.to_numpy(float)
    n = len(rth)
    for j in range(entry_i, n):
        if lo[j] <= stop:
            px = stop if j == entry_i else min(stop, o[j])
            return {"exit": px, "i": j, "why": "stop"}
        if red[j]:
            return {"exit": o[j + 1], "i": j + 1, "why": "signal"} if j + 1 < n else {"exit": c[j], "i": j, "why": "signal"}
    return {"exit": c[n - 1], "i": n - 1, "why": "close"}


def one_event(e, day: pd.DataFrame) -> dict | None:
    """The book's entry for one event and its three exits. None = the book takes no trade."""
    day = day.assign(ts=pd.to_datetime(day.ts, utc=True)).sort_values("ts").reset_index(drop=True)
    et = day.ts.dt.tz_convert(ET)
    day = day[(et.dt.hour * 60 + et.dt.minute) < 16 * 60].reset_index(drop=True)          # pre-market and the session
    rth = day[day.segment == "rth"].reset_index(drop=True)
    if not len(rth):
        return None
    cut, cost = latest(e.session), float(e.cost_rt)
    hit = pick(tape_setups(rth, pd.Timestamp(e.t0), bool(e.halt)), rth, cut, cost)
    if hit is None:
        return None
    s, t = hit
    now = score(rth, s, cut, cost)
    if now is None:
        return None
    m = matrix(day).set_index("ts").reindex(rth.ts.to_numpy())
    green = m[list(NAMES)].fillna(False).to_numpy(bool)
    entry_i = int(np.flatnonzero((rth.ts == t.entry_ts).to_numpy())[0])
    hist = int((day.ts < t.entry_ts).sum())
    row = {"symbol": e.symbol, "session": e.session, "kind": s.kind, "entry_ts": t.entry_ts, "entry": t.entry, "stop": s.stop,
           "cost": cost, "history": hist, "settled": bool(hist >= MIN_HISTORY),
           "green_at_entry": bool(entry_i > 0 and green[entry_i - 1].all()), "net_now": now["net"]}
    for name, red in (("macd", ~green[:, 0]), ("matrix", ~green.all(axis=1))):
        x = exit_on(rth, red, entry_i, t.entry, s.stop)
        row.update({f"net_{name}": x["exit"] / t.entry - 1 - cost, f"why_{name}": x["why"], f"min_{name}": int(x["i"] - entry_i),
                    f"first_{name}": bool(x["why"] == "signal" and x["i"] - entry_i <= 1)})
    return row


def _boot(sess: np.ndarray, val: np.ndarray) -> np.ndarray | None:
    keep = np.isfinite(val)
    sess, val = sess[keep], val[keep]
    u, inv = np.unique(sess, return_inverse=True)
    if len(u) < 2:
        return None
    s, n = np.bincount(inv, val, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    den = n[b].sum(1)
    return s[b].sum(1)[den > 0] / den[den > 0]


def leg(t: pd.DataFrame, name: str, alpha: float | None = None) -> dict:
    x = t[f"net_{name}"].to_numpy(float)
    out = {"trades": int(len(x)), "mean": float(x.mean()) if len(x) else None, "win": float((x > 0).mean()) if len(x) else None,
           "median": float(np.median(x)) if len(x) else None}
    if name != "now" and len(x):
        out.update(median_minutes=float(t[f"min_{name}"].median()), stopped=float((t[f"why_{name}"] == "stop").mean()),
                   sold_on_first_candle=float(t[f"first_{name}"].mean()), held_to_close=float((t[f"why_{name}"] == "close").mean()),
                   top_1pct_share_of_sum=float(np.sort(x)[-max(1, len(x) // 100):].sum() / x.sum()) if x.sum() != 0 else None)
        d = x - t.net_now.to_numpy(float)
        m = _boot(t.session.to_numpy(), d)
        out["vs_now"] = {"mean": float(d.mean()), "lo95": float(np.quantile(m, 0.025)) if m is not None else None,
                         "hi95": float(np.quantile(m, 0.975)) if m is not None else None}
    if alpha is not None:
        m = _boot(t.session.to_numpy(), x)
        out["lower"] = float(np.quantile(m, alpha)) if m is not None and len(m) else None
        out["pass"] = bool(out["lower"] is not None and out["lower"] > 0)
    return out


def evaluate(t: pd.DataFrame, only: list[str] | None = None) -> dict:
    tested = list(only) if only is not None else list(EXITS)
    alpha = ALPHA / max(1, len(tested))
    res: dict = {"trades": int(len(t)), "sessions": int(t.session.nunique()), "alpha_each": alpha, "now": leg(t, "now"), "passes": []}
    for name in EXITS:
        res[name] = leg(t, name, alpha if name in tested else None)
        if res[name].get("pass"):
            res["passes"].append(name)
    res["by_setup"] = {k: {n: leg(g, n) for n in ("now", *EXITS)} for k, g in t.groupby("kind")}
    res["settled_only"] = {n: leg(t[t.settled], n) for n in ("now", *EXITS)}
    res["unsettled"] = {n: leg(t[~t.settled], n) for n in ("now", *EXITS)}
    res["green_at_entry"] = {n: leg(t[t.green_at_entry], n) for n in ("now", *EXITS)}
    res["not_green_at_entry"] = {n: leg(t[~t.green_at_entry], n) for n in ("now", *EXITS)}
    return res


def collect(split: str) -> pd.DataFrame:
    store, rows = ParquetBarStore("data/alpaca"), []
    for e in events(split).itertuples(index=False):
        d = date.fromisoformat(e.session)
        f = store.read_frame(e.symbol, d, d)
        if not len(f):
            continue
        r = one_event(e, f)
        if r is not None:
            rows.append(r)
    return pd.DataFrame(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--holdout", action="store_true", help="the held-out events, once, only for an exit that passed on training")
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
            print("the held-out events are opened only for an exit that passed on training")
            return 2
    t = collect(split)
    if t.empty:
        raise SystemExit("no trades: nothing reported")
    res = {"prereg": "docs/findings/prereg_trend_matrix_exit_2026-11-18.md", "split": split, **evaluate(t, only)}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1, default=str) + "\n")
    t.to_csv(a.out / f"{split}_trades.csv", index=False)
    print(json.dumps(res, indent=1, default=str)[:4000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
