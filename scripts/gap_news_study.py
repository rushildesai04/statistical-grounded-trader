"""Pre-market gaps by kind of news, a scanner input (docs/findings/prereg_gap_news_2026-11-18.md).

    .venv/bin/python scripts/gap_news_study.py              # training, run ONCE
    .venv/bin/python scripts/gap_news_study.py --holdout    # only a cell that passed on training, once

Report only: nothing here changes a book. Headlines are external text: read as data, classed by fixed patterns."""
from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.decision.features import CostModelAdapter
from sgt.fast import news as news_mod
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/gap_news")
GAPPERS = Path("data/universe/gappers_2y.csv")
NEWS = Path("data/news")
SPLIT = "2026-04-01"
MIN_PREV_VOLUME, LOCATE = 50_000, 0.005
EXITS = ("60", "close")
MIN_CELL = 150
ALPHA, SEED, N_BOOT = 0.05, 20261118, 10_000
ET = ZoneInfo("America/New_York")
PATTERNS = (
    ("OFFERING", re.compile(r"offering|\bpriced\b|pricing of|private placement|registered direct|warrant|at-the-market|"
                            r"\bshelf\b|convertible|dilut", re.I)),
    ("EARNINGS", news_mod.EARN),
    ("CLINICAL", re.compile(r"\bfda\b|phase (?:1|2|3|i{1,3})\b|\btrial|clinical|topline|approval|clearance|patent", re.I)),
    ("DEAL", re.compile(r"merger|acquir|acquisition|buyout|agreement|contract|partnership|collaboration|\baward|\border", re.I)),
    ("ANALYST", re.compile(r"upgrade|downgrade|price target|initiates|reiterates|maintains", re.I)),
)
KINDS = tuple(k for k, _ in PATTERNS) + ("OTHER", "NONE")
GAP_BANDS = (("gap 10-20%", 10, 20), ("gap 20-50%", 20, 50), ("gap above 50%", 50, np.inf))
PRICE_BANDS = (("under $1", 0, 1), ("$1-5", 1, 5), ("above $5", 5, np.inf))


def kind_of(headlines: list[str]) -> str:
    """The kind of a gap from its company headlines: the first pattern (in the frozen order) any headline matches."""
    if not headlines:
        return "NONE"
    for name, pat in PATTERNS:
        if any(pat.search(h or "") for h in headlines):
            return name
    return "OTHER"


class Headlines:
    """Company headlines per symbol from the monthly archive (articles tagging at most MAX_OWN_SYMBOLS symbols)."""

    def __init__(self, root: Path = NEWS) -> None:
        self.root, self.months = root, {}

    def _month(self, key: str) -> dict[str, list[tuple[pd.Timestamp, str]]]:
        if key not in self.months:
            p = self.root / f"{key}.parquet"
            out: dict[str, list] = {}
            if p.is_file():
                n = pd.read_parquet(p, columns=["created_at", "symbols", "headline"])
                ts = pd.to_datetime(n.created_at, utc=True, errors="coerce")
                for t, syms, h in zip(ts, n.symbols.fillna(""), n.headline.fillna("")):
                    tagged = [s for s in str(syms).split(",") if s]
                    if t is pd.NaT or not tagged or len(tagged) > news_mod.MAX_OWN_SYMBOLS:
                        continue
                    for s in tagged:
                        out.setdefault(s, []).append((t, str(h)[:300]))
            self.months[key] = out
            if len(self.months) > 4:
                self.months.pop(next(iter(self.months)))
        return self.months[key]

    def before_open(self, symbol: str, session: str) -> list[str]:
        """Headlines created from 16:00 ET of the previous weekday to 09:30 ET of ``session``."""
        d = date.fromisoformat(session)
        hi = pd.Timestamp(datetime.combine(d, time(9, 30), ET))
        lo = pd.Timestamp(news_mod.window_start(hi.to_pydatetime()))
        keys = {f"{lo.tz_convert('UTC'):%Y-%m}", f"{hi.tz_convert('UTC'):%Y-%m}"}
        return [h for k in sorted(keys) for t, h in self._month(k).get(symbol, []) if lo <= t < hi]


def trade(rth: pd.DataFrame) -> dict | None:
    """From the first regular candle's open: the move to the close one hour later and to the session's close."""
    if rth.empty:
        return None
    o = float(rth.open.iloc[0])
    if not o > 0:
        return None
    t0 = rth.ts.iloc[0]
    hour = rth[rth.ts <= t0 + pd.Timedelta(minutes=59)]
    return {"ts": t0, "px": o, "g_60": float(hour.close.iloc[-1]) / o - 1, "g_close": float(rth.close.iloc[-1]) / o - 1}


def collect(g: pd.DataFrame, store: ParquetBarStore, heads: Headlines, cost) -> pd.DataFrame:
    rows = []
    for r in g.itertuples(index=False):
        d = date.fromisoformat(r.session)
        f = store.read_frame(r.symbol, d, d)
        if not len(f):
            continue
        rth = f[f.segment == "rth"].assign(ts=lambda x: pd.to_datetime(x.ts, utc=True)).sort_values("ts").reset_index(drop=True)
        t = trade(rth)
        if t is None:
            continue
        rows.append({"symbol": r.symbol, "session": r.session, "gap": float(r.gap_pct), "liquid": bool(r.prev_volume >= MIN_PREV_VOLUME),
                     "kind": kind_of(heads.before_open(r.symbol, r.session)), "px": t["px"], "g_60": t["g_60"], "g_close": t["g_close"],
                     "cost": float(cost.round_trip(r.symbol, t["ts"].to_pydatetime(), t["px"])[0])})
    return pd.DataFrame(rows)


class Boot:
    def __init__(self, days: np.ndarray) -> None:
        self.u = np.unique(days)
        self.idx = np.random.default_rng(SEED).integers(0, len(self.u), size=(N_BOOT, len(self.u)), dtype=np.int32)

    def lower(self, days: np.ndarray, val: np.ndarray, alpha: float) -> float | None:
        if len(val) < 2 or len(self.u) < 2:
            return None
        pos = np.searchsorted(self.u, days)
        s, n = np.bincount(pos, val, len(self.u)), np.bincount(pos, minlength=len(self.u)).astype(float)
        den = n[self.idx].sum(1)
        ok = den > 0
        return float(np.quantile(s[self.idx].sum(1)[ok] / den[ok], alpha)) if ok.any() else None


def cell(g: pd.DataFrame, e: str, boot: Boot | None = None, alpha: float | None = None) -> dict:
    x, c = g[f"g_{e}"].to_numpy(float), g.cost.to_numpy(float)
    out = {"gaps": int(len(x))}
    if not len(x):
        return out
    out.update(gross=float(x.mean()), up_share=float((x > 0).mean()), long_net=float((x - c).mean()),
               short_net=float((-x - c - LOCATE).mean()))
    if boot is not None and alpha is not None:
        ok = len(x) >= MIN_CELL
        lo_l = boot.lower(g.session.to_numpy(), x - c, alpha) if ok else None
        lo_s = boot.lower(g.session.to_numpy(), -x - c - LOCATE, alpha) if ok else None
        out.update(long_lower=lo_l, short_lower=lo_s, long_pass=bool(lo_l is not None and lo_l > 0),
                   short_pass=bool(lo_s is not None and lo_s > 0))
    return out


def evaluate(t: pd.DataFrame, only: list[str] | None = None) -> dict:
    main = t[t.liquid]
    n_tests = len(only) if only else len(KINDS) * len(EXITS) * 2
    alpha = ALPHA / n_tests
    boot = Boot(main.session.to_numpy())
    res: dict = {"gaps": int(len(main)), "sessions": int(main.session.nunique()), "alpha_each": alpha, "cells": {}, "passes": [],
                 "share_by_kind": {k: float((main.kind == k).mean()) for k in KINDS}}
    for k in KINDS:
        g = main[main.kind == k]
        for e in EXITS:
            key = f"{k} | {e}"
            if only is not None and not any(o.startswith(key + " |") for o in only):
                continue
            c = cell(g, e, boot, alpha)
            c["by_gap"] = {n: cell(g[(g.gap >= a) & (g.gap < b)], e) for n, a, b in GAP_BANDS}
            c["by_price"] = {n: cell(g[(g.px >= a) & (g.px < b)], e) for n, a, b in PRICE_BANDS}
            res["cells"][key] = c
            for side in ("long", "short"):
                if c.get(f"{side}_pass") and (only is None or f"{key} | {side}" in only):
                    res["passes"].append(f"{key} | {side}")
    thin = t[~t.liquid]
    res["thin_apart"] = {f"{k} | {e}": cell(thin[thin.kind == k], e) for k in KINDS for e in EXITS}
    res["all_kinds"] = {e: cell(main, e) for e in EXITS}
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--holdout", action="store_true", help="the held-out sessions, once, only for a cell that passed on training")
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
            print("the held-out sessions are opened only for a cell that passed on training")
            return 2
    g = pd.read_csv(GAPPERS, converters={"symbol": str})
    g = g[g.session >= SPLIT] if a.holdout else g[g.session < SPLIT]
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT, rest_mult=GAPPER_REST_MULT)
    t = collect(g.sort_values(["session", "symbol"]), ParquetBarStore("data/alpaca"), Headlines(), cost)
    if t.empty:
        raise SystemExit("no gaps: nothing reported")
    res = {"prereg": "docs/findings/prereg_gap_news_2026-11-18.md", "split": split, **evaluate(t, only)}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    t.to_csv(a.out / f"{split}_gaps.csv", index=False)
    print(json.dumps({k: v for k, v in res.items() if k not in ("cells", "thin_apart")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
