"""What an alerted stock does after the close: overnight to five days
(docs/findings/prereg_alert_after_day_2026-11-17.md).

    .venv/bin/python scripts/alert_after_day_study.py --fetch     # adjusted daily bars per alert symbol (cached, spaced)
    .venv/bin/python scripts/alert_after_day_study.py             # training map, run ONCE

Report only: nothing here changes a book. The bar API is external: rows are parsed strictly and bounded."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from fade_challengers import events

OUT = Path("reports/alert_after_day")
CACHE = Path("data/alert_daily")
NEWS = {"train": Path("reports/fade_news/dev_events.csv"), "holdout": Path("reports/fade_news/holdout_events.csv")}
START, END = "2024-12-01", "2026-11-14"
HORIZONS = ("overnight", "next_session", "one_day", "two_days", "five_days")
GROUPS = ("all", "no_news", "news", "strong_close", "weak_close")
CLIP = (-0.95, 3.0)
MIN_N, ALPHA, SEED, N_BOOT = 300, 0.05, 20261117, 10_000
MAX_ROWS, MIN_INTERVAL_S, TIMEOUT_S, RETRIES = 1000, 0.5, 30.0, 5


def parse_daily(rows: list) -> pd.DataFrame:
    """API rows -> date-indexed open/high/low/close; malformed or non-positive rows raise (nothing is guessed)."""
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("daily bars: not a list or too many rows")
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close"])
    d = pd.DataFrame({k: [float(r[s]) for r in rows] for k, s in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))})
    d.index = pd.to_datetime([r["t"] for r in rows], utc=True).tz_convert("America/New_York").strftime("%Y-%m-%d")
    px = d.to_numpy(float)
    if not np.isfinite(px).all() or (px <= 0).any() or (d.high < d.low).any() or d.index.duplicated().any():
        raise ValueError("daily bars: bad price or duplicate day")
    return d.sort_index()


def fetch(sym: str) -> pd.DataFrame | None:
    """Adjusted daily bars of one symbol (cached, an empty answer included); None when the request fails."""
    import httpx

    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    path = CACHE / f"{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars"
    params = {"symbols": sym, "timeframe": "1Day", "start": START, "end": END, "adjustment": "all", "feed": "sip",
              "limit": MAX_ROWS}
    for attempt in range(RETRIES):
        time.sleep(MIN_INTERVAL_S)
        try:
            r = httpx.get(url, params=params, headers=_alpaca_headers(), timeout=TIMEOUT_S)
        except httpx.TransportError:
            continue
        if r.status_code == 200:
            try:
                d = parse_daily((r.json().get("bars") or {}).get(sym) or [])
            except (ValueError, KeyError, TypeError):
                return None
            CACHE.mkdir(parents=True, exist_ok=True)
            d.to_parquet(path)
            return d
        if r.status_code not in (429, 500, 502, 503, 504):
            return None
        time.sleep(2.0 * 2 ** attempt)
    return None


def after(d: pd.DataFrame, day: str) -> dict | None:
    """Returns after the close of ``day`` from one adjusted series; None when a needed bar is missing."""
    if day not in d.index:
        return None
    i = d.index.get_loc(day)
    if not isinstance(i, int) or i + 5 >= len(d):
        return None
    c0, hi, lo = float(d.close.iloc[i]), float(d.high.iloc[i]), float(d.low.iloc[i])
    o1, c1, c2, c5 = (float(x) for x in (d.open.iloc[i + 1], d.close.iloc[i + 1], d.close.iloc[i + 2], d.close.iloc[i + 5]))
    return {"overnight": o1 / c0 - 1, "next_session": c1 / o1 - 1, "one_day": c1 / c0 - 1, "two_days": c2 / c0 - 1,
            "five_days": c5 / c0 - 1, "close_pos": (c0 - lo) / (hi - lo) if hi > lo else 0.5}


def collect(split: str, bars) -> tuple[pd.DataFrame, dict]:
    ev = events(split)
    news = pd.read_csv(NEWS[split], usecols=["symbol", "session", "news_own"]).drop_duplicates(["symbol", "session"])
    ev = ev.assign(session=ev.session.astype(str)).merge(news.assign(session=news.session.astype(str)),
                                                         on=["symbol", "session"], how="left")
    rows, miss = [], {"no_bars": 0, "no_window": 0}
    for r in ev.itertuples(index=False):
        d = bars(r.symbol)
        if d is None or d.empty:
            miss["no_bars"] += 1
            continue
        a = after(d, r.session)
        if a is None:
            miss["no_window"] += 1
            continue
        rows.append({"symbol": r.symbol, "session": r.session, "cost": float(r.cost_rt),
                     "news": None if pd.isna(r.news_own) else bool(r.news_own), **a})
    return pd.DataFrame(rows), {"events": int(len(ev)), **miss}


def members(t: pd.DataFrame, g: str) -> pd.Series:
    if g == "all":
        return pd.Series(True, index=t.index)
    if g == "no_news":
        return t.news == False  # noqa: E712 - unknown news is in neither news group
    if g == "news":
        return t.news == True  # noqa: E712
    return t.close_pos >= 2 / 3 if g == "strong_close" else t.close_pos <= 1 / 3


def bounds(v: np.ndarray, s: np.ndarray, alpha: float) -> tuple[float, float]:
    """Session-bootstrap (lower, upper) quantiles at alpha of the mean of ``v`` (sessions resampled whole)."""
    g = pd.DataFrame({"v": v, "s": s}).groupby("s").v.agg(["sum", "count"])
    idx = np.random.default_rng(SEED).integers(0, len(g), size=(N_BOOT, len(g)))
    m = g["sum"].to_numpy()[idx].sum(1) / g["count"].to_numpy()[idx].sum(1)
    return float(np.quantile(m, alpha)), float(np.quantile(m, 1 - alpha))


def cells(t: pd.DataFrame) -> list[dict]:
    alpha = ALPHA / (len(HORIZONS) * len(GROUPS))
    out = []
    for g in GROUPS:
        x = t[members(t, g)]
        for h in HORIZONS:
            raw = x[h].to_numpy(float)
            ret = np.clip(raw, *CLIP)
            net = ret - x.cost.to_numpy(float)
            lo, up = bounds(net, x.session.to_numpy(), alpha) if len(x) >= 2 else (float("nan"), float("nan"))
            out.append({"group": g, "horizon": h, "events": int(len(x)), "mean": float(ret.mean()) if len(x) else None,
                        "median": float(np.median(ret)) if len(x) else None, "up": float((ret > 0).mean()) if len(x) else None,
                        "long_net": float(net.mean()) if len(x) else None, "lower": lo, "upper": up,
                        "clipped": int(((raw < CLIP[0]) | (raw > CLIP[1])).sum()),
                        "finding_long": bool(len(x) >= MIN_N and net.mean() > 0 and lo > 0),
                        "short_side_note": bool(len(x) >= MIN_N and (ret + x.cost.to_numpy(float)).mean() < 0
                                                and up + 2 * float(x.cost.mean()) < 0)})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="only fetch and cache the daily bars (no number is computed)")
    a = ap.parse_args(argv)
    res_path = OUT / "result_train.json"
    if not a.fetch and res_path.is_file():
        print(f"{res_path} exists: the training map was made once and is not repeated")
        return 2
    if a.fetch:
        syms = sorted(set(events("train").symbol) | set(events("holdout").symbol))
        bad = 0
        for i, s in enumerate(syms, 1):
            bad += fetch(s) is None
            if i % 200 == 0:
                print(f"{i} of {len(syms)} symbols, {bad} failed", flush=True)
        print(f"done: {len(syms)} symbols, {bad} failed", flush=True)
        return 0
    cache: dict[str, pd.DataFrame | None] = {}

    def bars(sym: str):
        if sym not in cache:
            p = CACHE / f"{sym}.parquet"
            cache[sym] = pd.read_parquet(p) if p.exists() else None
        return cache[sym]

    t, cov = collect("train", bars)
    res = {"prereg": "docs/findings/prereg_alert_after_day_2026-11-17.md", "coverage": cov, "scored": int(len(t)),
           "cells": cells(t)}
    found = [c for c in res["cells"] if c["finding_long"]]
    res["findings_long"] = found
    if found:                                               # the held-out period is read only for a finding
        h, hcov = collect("holdout", bars)
        keep = {(c["group"], c["horizon"]) for c in found}
        res["holdout"] = {"coverage": hcov, "cells": [c for c in cells(h) if (c["group"], c["horizon"]) in keep]}
    OUT.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
