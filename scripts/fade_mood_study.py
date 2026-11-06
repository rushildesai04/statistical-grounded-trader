"""Market mood at the alert vs the fade (prereg_fade_mood_2026-11-06).

    python scripts/fade_mood_study.py      # run once: training gate + held-out secondary
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/fade_mood")
SEED, N_BOOT, ALPHA, MIN_N, CUT = 20261106, 10_000, 0.05, 100, 0.01


def mood(store: ParquetBarStore, etf: str, session: str, entry_ts: pd.Timestamp, cache: dict) -> float | None:
    """ETF return from the 09:30 open to the close of the last RTH bar that ENDED before the entry."""
    key = (etf, session)
    if key not in cache:
        d = date.fromisoformat(session)
        f = store.read_frame(etf, d, d)
        cache[key] = f[f.segment == "rth"].assign(ts=lambda x: pd.to_datetime(x.ts, utc=True)).sort_values("ts") \
            if len(f) else f
    f = cache[key]
    if f.empty:
        return None
    done = f[f.ts + pd.Timedelta(minutes=1) <= entry_ts]
    if done.empty:
        return 0.0                                     # entry in the first minute: no move yet
    return float(done.close.iloc[-1] / f.open.iloc[0] - 1)


def group(m: float | None) -> str | None:
    return None if m is None else ("on" if m >= CUT else "off" if m <= -CUT else "neutral")


def events(name: str, store: ParquetBarStore) -> pd.DataFrame:
    e = pd.read_csv(f"reports/fade_news/{name}_events.csv")
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "entry_ts"]]
    e = e.merge(src, on=["symbol", "session"], how="left").dropna(subset=["net", "entry_ts"])
    cache: dict = {}
    for etf in ("IWM", "SPY"):
        e[f"m_{etf}"] = [mood(store, etf, str(r.session), pd.Timestamp(r.entry_ts), cache) for r in e.itertuples()]
    return e


def evaluate(e: pd.DataFrame, etf: str) -> dict:
    t = e.dropna(subset=[f"m_{etf}"]).assign(g=lambda d: d[f"m_{etf}"].map(group))
    rng = np.random.default_rng(SEED)
    on = (t.g == "on").to_numpy()
    sess = t.session.astype(str).to_numpy()
    u, inv = np.unique(sess, return_inverse=True)
    net = t.net.to_numpy(float)
    s1, n1 = np.bincount(inv, net * on, len(u)), np.bincount(inv, on.astype(float), len(u))
    s0, n0 = np.bincount(inv, net * ~on, len(u)), np.bincount(inv, (~on).astype(float), len(u))
    b = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        diff = s1[b].sum(1) / n1[b].sum(1) - s0[b].sum(1) / n0[b].sum(1)
    skip = (-s1)[b].mean(1)                            # per-session gain from skipping risk-on fades
    groups = {k: {"n": int((t.g == k).sum()), "mean": float(t.net[t.g == k].mean()) if (t.g == k).any() else None}
              for k in ("on", "neutral", "off")}
    res = {"groups": groups, "diff": float(np.nanmean(diff)), "diff_upper": float(np.nanquantile(diff, 1 - ALPHA)),
           "skip_gain": float(skip.mean()), "skip_lower": float(np.quantile(skip, ALPHA)), "coverage": float(len(t) / len(e))}
    res["pass"] = bool(groups["on"]["n"] >= MIN_N and res["diff_upper"] < 0 and res["skip_lower"] > 0)
    return res


def main() -> int:
    store = ParquetBarStore("data/alpaca")
    out = {}
    for name in ("dev", "holdout"):
        e = events(name, store)
        out[name] = {etf: evaluate(e, etf) for etf in ("IWM", "SPY")}
        for etf, r in out[name].items():
            g = r["groups"]
            print(f"{name:7s} {etf}: risk-on n{g['on']['n']} {g['on']['mean'] or 0:+.2%} | neutral n{g['neutral']['n']} "
                  f"{g['neutral']['mean'] or 0:+.2%} | risk-off n{g['off']['n']} {g['off']['mean'] or 0:+.2%} | diff "
                  f"{r['diff']:+.2%} (upper {r['diff_upper']:+.2%}) skip gain/session {r['skip_gain']:+.3%} (lower "
                  f"{r['skip_lower']:+.3%}) coverage {r['coverage']:.0%} -> "
                  f"{'PASS' if r['pass'] else 'fail'}{' (decides)' if name == 'dev' and etf == 'IWM' else ''}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-mood-2026-11-06-dev", kind="fade-mood-dev",
                                  params={"prereg": "docs/findings/prereg_fade_mood_2026-11-06.md"}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
