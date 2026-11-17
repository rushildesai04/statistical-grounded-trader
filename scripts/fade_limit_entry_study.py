"""Short the squeeze, not the first print: a sell limit above the market at entry
(docs/findings/prereg_fade_limit_entry_2026-11-17.md).

    .venv/bin/python scripts/fade_limit_entry_study.py      # training, run ONCE

Report only: nothing here changes a book."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from fade_challengers import N_BOOT, TAIL_PP, _rth, events
from sgt.fast.exits import CHAMPION, indicators, simulate
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/fade_limit_entry")
VARIANTS = (("LIM-2-3", 0.02, 3), ("LIM-5-3", 0.05, 3), ("LIM-5-5", 0.05, 5), ("LIM-10-5", 0.10, 5))
MAX_DELAY = pd.Timedelta(minutes=5)             # the research's tolerance for a late bar
SEED, ALPHA = 20261105, 0.05


def limit_entry(bars: pd.DataFrame, entry_ts: pd.Timestamp, p0: float, x: float, n: int) -> tuple[pd.Timestamp, float, str] | None:
    """(entry bar start, entry price, "limit" | "late") for a sell limit at p0 x (1 + x) resting ``n`` minutes from
    the champion's entry bar; None = no entry at all (no bar to enter late on)."""
    level = p0 * (1 + x)
    end = entry_ts + pd.Timedelta(minutes=n)
    w = bars[(bars.ts >= entry_ts) & (bars.ts < end)]
    for b in w.itertuples():
        if b.ts > entry_ts and b.open >= level:               # a later bar opening through the offer: filled there
            return b.ts, float(b.open), "limit"
        if b.high > level:                                     # traded THROUGH the offer (a touch is not a fill)
            return b.ts, float(level), "limit"
    later = bars[bars.ts >= end]
    if later.empty or later.ts.iloc[0] - end > MAX_DELAY:
        return None
    return later.ts.iloc[0], float(later.open.iloc[0]), "late"


def score(ev: pd.DataFrame, store: ParquetBarStore) -> pd.DataFrame:
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, str(r.session))
        if bars.empty:
            continue
        ind = indicators(bars)
        ts, p0, cost = pd.Timestamp(r.entry_ts), float(r.entry), float(r.cost_rt) + LOCATE
        c = simulate(ind, ts, p0, CHAMPION)
        row = {"symbol": r.symbol, "session": str(r.session), "net_champion": np.nan if c is None else c["gross"] - cost,
               "how_champion": None if c is None else c["how"]}
        for name, x, n in VARIANTS:
            e = limit_entry(bars, ts, p0, x, n)
            s = None if e is None else simulate(ind, e[0], e[1], CHAMPION)
            row[f"net_{name}"] = np.nan if s is None else s["gross"] - cost
            row[f"how_{name}"] = None if s is None else s["how"]
            row[f"via_{name}"] = None if e is None else e[2]
        rows.append(row)
    return pd.DataFrame(rows)


def paired(t: pd.DataFrame, name: str, alpha: float) -> dict:
    """Total net per session, a leg without a trade counting 0; paired difference against the champion."""
    col = f"net_{name}"
    d = t[["session"]].assign(a=t[col].fillna(0.0), c=t.net_champion.fillna(0.0)).groupby("session")[["a", "c"]].sum()
    diff = (d.a - d.c).to_numpy(float)
    means = diff[np.random.default_rng(SEED).integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1)
    x, c = t[col].dropna(), t.net_champion.dropna()
    via = t[f"via_{name}"]
    out = {"sessions": int(len(diff)), "mean_daily_diff": float(diff.mean()), "lower": float(np.quantile(means, alpha)),
           "upper": float(np.quantile(means, 1 - alpha)), "n": int(len(x)), "mean": float(x.mean()), "win": float((x > 0).mean()),
           "p01": float(np.quantile(x, 0.01)), "champ_mean": float(c.mean()), "champ_p01": float(np.quantile(c, 0.01)),
           "filled_share": float((via == "limit").mean()), "stopped": float((t[f"how_{name}"] == "stop").sum() / max(1, len(x)))}
    for tag in ("limit", "late"):
        g = t[via == tag].dropna(subset=[col, "net_champion"])
        out[tag] = {"n": int(len(g)), "variant": float(g[col].mean()) if len(g) else None,
                    "champion": float(g.net_champion.mean()) if len(g) else None,
                    "champion_stopped": float((g.how_champion == "stop").mean()) if len(g) else None}
    only = t[col].where(via == "limit")                       # "not filled = no trade"
    out["limit_only"] = {"n": int(only.notna().sum()), "mean": float(only.mean()) if only.notna().any() else None,
                         "total": float(only.fillna(0.0).sum()), "champion_total": float(c.sum())}
    out["pass"] = bool(out["lower"] > 0 and out["p01"] >= out["champ_p01"] - TAIL_PP)
    return out


def evaluate(t: pd.DataFrame) -> dict:
    return {name: paired(t, name, ALPHA / len(VARIANTS)) for name, _, _ in VARIANTS}


def main() -> int:
    res_path = OUT / "dev.json"
    if res_path.is_file():
        print(f"{res_path} exists: the training run was made once and is not repeated")
        return 2
    from alert_fade_test import score as research_score

    store = ParquetBarStore("data/alpaca")
    ev = events("train")
    t = score(ev, store)
    ref = research_score(ev, store).net.to_numpy(float)
    if len(t) != len(ref) or not np.allclose(t.net_champion.to_numpy(float), ref, equal_nan=True, rtol=0, atol=1e-12):
        raise SystemExit("champion does not reproduce alert_fade_test.score: nothing reported")
    res = {"prereg": "docs/findings/prereg_fade_limit_entry_2026-11-17.md", "events": int(len(t)), "dev": evaluate(t)}
    ho = score(events("holdout"), store)
    res["holdout_secondary"] = evaluate(ho)
    news = Path("reports/fade_news/dev_events.csv")
    if news.is_file():
        n = pd.read_csv(news, usecols=["symbol", "session", "news_own"]).drop_duplicates(["symbol", "session"])
        m = t.merge(n.assign(session=n.session.astype(str)), on=["symbol", "session"], how="left")
        res["no_news_dev"] = evaluate(m[m.news_own == 0])
    res["passed"] = [k for k, v in res["dev"].items() if v["pass"]]
    OUT.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
