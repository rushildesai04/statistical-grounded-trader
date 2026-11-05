"""Fade news filter study (prereg_fade_news_2026-11-05): company news before the alert vs the fade's result.

    python scripts/fade_news_study.py dev       # development events 2025-01..2026-06: gate H1
    python scripts/fade_news_study.py holdout   # held-out 2026-07..10: confirmation (filter) + confidence table
    python scripts/fade_news_study.py conf      # amendment A1: P(group mean net > 0) over dev + held-out events
"""
from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import score
from fade_meta_study import paired_lower, per_session
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import news
from sgt.intraday.store import ParquetBarStore

SRC = Path("reports/telegram/trades_pooled_first.csv")
OUT = Path("reports/fade_news")
SEED, N_BOOT, MIN_N = 20261105, 10_000, 100
GROUPS = ("news_any", "news_own", "news_earn")


def archive(symbols: set[str]) -> dict[str, pd.DataFrame]:
    parts = []
    for f in sorted(glob.glob("data/news/*.parquet")):
        d = pd.read_parquet(f, columns=["created_at", "symbols", "headline"])
        parts.append(d[d.symbols.fillna("").map(lambda s: bool(symbols & set(s.split(","))))])
    a = pd.concat(parts, ignore_index=True)
    a["sym"] = a.symbols.str.split(",")
    x = a.explode("sym")
    return {s: g.drop(columns="sym") for s, g in x[x.sym.isin(symbols)].groupby("sym")}


def events(split: str) -> pd.DataFrame:
    ev = pd.read_csv(SRC)
    ev = ev[(ev.split == split) & (ev.status == "ok")].reset_index(drop=True)
    ev["net"] = score(ev, ParquetBarStore("data/alpaca")).net.to_numpy()
    ev = ev.dropna(subset=["net"]).reset_index(drop=True)
    arc = archive(set(ev.symbol))
    empty = pd.DataFrame(columns=["created_at", "symbols", "headline"])
    rows = [news.flags(r.symbol, pd.Timestamp(r.posted_at).to_pydatetime(), arc.get(r.symbol, empty))
            for r in ev.itertuples(index=False)]
    return ev.join(pd.DataFrame(rows))


def upper_mean(x: np.ndarray, sessions: np.ndarray, rng) -> float:
    """Session-bootstrap one-sided 95% upper bound of the mean."""
    u = np.unique(sessions)
    idx = {s: np.flatnonzero(sessions == s) for s in u}
    means = np.empty(N_BOOT)
    for i in range(N_BOOT):
        pick = rng.choice(u, size=len(u))
        means[i] = x[np.concatenate([idx[s] for s in pick])].mean()
    return float(np.quantile(means, 0.95))


def table(ev: pd.DataFrame) -> dict:
    out = {"all": {"n": len(ev), "mean": float(ev.net.mean()), "win": float((ev.net > 0).mean())}}
    for g in GROUPS:
        for flag in (True, False):
            sub = ev[ev[g] == flag]
            out[f"{g}={int(flag)}"] = {"n": len(sub), "mean": float(sub.net.mean()) if len(sub) else None,
                                      "win": float((sub.net > 0).mean()) if len(sub) else None}
    return out


def report(name: str, ev: pd.DataFrame, rng) -> dict:
    t = table(ev)
    own = ev[ev.news_own]
    base = per_session(ev, np.ones(len(ev)))
    skip = per_session(ev, (~ev.news_own).to_numpy()).reindex(base.index, fill_value=0.0)
    res = {"table": t, "own_upper95": upper_mean(own.net.to_numpy(float), own.session.astype(str).to_numpy(), rng)
           if len(own) else None,
           "per_session_all": float(base.mean()), "per_session_skip": float(skip.mean()),
           "skip_lower95": paired_lower((skip - base).to_numpy(float), rng)}
    print(f"{name}: {len(ev)} events {ev.session.min()} .. {ev.session.max()}")
    for k, v in t.items():
        m = "-" if v["mean"] is None else f"{v['mean']:+.2%}"
        w = "-" if v["win"] is None else f"{v['win']:.0%}"
        print(f"  {k:12s} n {v['n']:5d}  mean net {m:>7s}  win {w}")
    print(f"  news_own upper95 {res['own_upper95']:+.2%} | net/session all {res['per_session_all']:+.3%} "
          f"skip-news {res['per_session_skip']:+.3%} (paired lower95 {res['skip_lower95']:+.3%})")
    return res


def cmd_dev() -> int:
    rng = np.random.default_rng(SEED)
    ev = events("train")
    res = report("DEV", ev, rng)
    n_own = res["table"]["news_own=1"]["n"]
    ok = n_own >= MIN_N and res["own_upper95"] is not None and res["own_upper95"] < 0 and res["skip_lower95"] > 0
    res["verdict"] = "PASS" if ok else "FAIL"
    print(f"VERDICT (dev gate H1): {res['verdict']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(res, indent=2))
    ev[["symbol", "session", "alert_type", "posted_at", "net", *GROUPS, "news_n", "headline"]].to_csv(
        OUT / "dev_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-news-2026-11-05-dev", kind="fade-news-dev",
                                  params={"prereg": "docs/findings/prereg_fade_news_2026-11-05.md"}, metrics=res)
    return 0


def cmd_holdout() -> int:
    dev = json.loads((OUT / "dev.json").read_text())
    rng = np.random.default_rng(SEED + 1)
    ev = events("holdout")
    res = report("HELD-OUT", ev, rng)
    own = res["table"]["news_own=1"]
    confirm = dev["verdict"] == "PASS" and own["mean"] is not None and own["mean"] < 0 and \
        res["per_session_skip"] > res["per_session_all"]
    res["filter"] = "ON" if confirm else "OFF"
    print(f"news filter (dev {dev['verdict']}, held-out confirmation): {res['filter']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "holdout.json").write_text(json.dumps(res, indent=2))
    ev[["symbol", "session", "alert_type", "posted_at", "net", *GROUPS, "news_n", "headline"]].to_csv(
        OUT / "holdout_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-news-2026-11-05-holdout", kind="fade-news-holdout",
                                  params={"prereg": "docs/findings/prereg_fade_news_2026-11-05.md"}, metrics=res)
    return 0


def group(r) -> str:
    return "earn" if r.news_earn else ("own" if r.news_own else "none")


def p_positive(x: np.ndarray, sessions: np.ndarray, rng) -> float:
    """Share of session-bootstrap means > 0."""
    u = np.unique(sessions)
    idx = {s: np.flatnonzero(sessions == s) for s in u}
    hits = 0
    for _ in range(N_BOOT):
        pick = rng.choice(u, size=len(u))
        hits += x[np.concatenate([idx[s] for s in pick])].mean() > 0
    return hits / N_BOOT


def cmd_conf() -> int:
    ev = pd.concat([pd.read_csv(OUT / "dev_events.csv"), pd.read_csv(OUT / "holdout_events.csv")], ignore_index=True)
    ev["group"] = [group(r) for r in ev.itertuples(index=False)]
    rng = np.random.default_rng(SEED)
    res = {}
    for g, sub in [("all", ev), *((g, ev[ev.group == g]) for g in ("none", "own", "earn"))]:
        res[g] = {"n": len(sub), "conf": round(100 * p_positive(sub.net.to_numpy(float),
                                                                sub.session.astype(str).to_numpy(), rng)),
                  "win": float((sub.net > 0).mean()), "mean": float(sub.net.mean())}
        print(f"  {g:5s} n {len(sub):5d}  confidence {res[g]['conf']:3d}/100  win {res[g]['win']:.0%}  "
              f"avg {res[g]['mean']:+.2%}")
    (OUT / "confidence.json").write_text(json.dumps(res, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "holdout", "conf"])
    return {"dev": cmd_dev, "holdout": cmd_holdout, "conf": cmd_conf}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
