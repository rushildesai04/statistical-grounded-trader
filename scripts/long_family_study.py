"""Long trades on the channel's alerts, family L1-L3 (prereg_long_family_2026-11-09).

    python scripts/long_family_study.py      # run once: training gate + held-out (not independent for L2/L3)
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fade_volsize_study import CALM, sigma  # noqa: E402

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/long_family")
SEED, N_BOOT, ALPHA, STOP, TARGET, HOLD = 20261109, 10_000, 0.05 / 3, 0.05, 0.05, 15
D_STAR = "2026-07-01"
REOPEN_MAX = pd.Timedelta(minutes=30)


def long_trade(bars: pd.DataFrame, t0: pd.Timestamp, stop: float = STOP, target: float = TARGET,
               hold: int = HOLD) -> float | None:
    """Gross return of a long bought at the open of the bar starting at ``t0``; stop first within a bar."""
    w = bars[(bars.ts >= t0) & (bars.ts < t0 + pd.Timedelta(minutes=hold))]
    if w.empty or w.ts.iloc[0] != t0:
        return None
    entry = float(w.open.iloc[0])
    if entry <= 0:
        return None
    lo, hi = entry * (1 - stop), entry * (1 + target)
    for b in w.itertuples():
        if b.low <= lo:
            return min(lo, float(b.open)) / entry - 1
        if b.high >= hi:
            return max(hi, float(b.open)) / entry - 1
    return float(w.close.iloc[-1]) / entry - 1


class Bars:
    def __init__(self) -> None:
        self.store, self.cache = ParquetBarStore("data/alpaca"), {}

    def __call__(self, symbol: str, session: str) -> pd.DataFrame:
        k = (symbol, session)
        if k not in self.cache:
            d = date.fromisoformat(session)
            f = self.store.read_frame(symbol, d, d)
            self.cache[k] = (f[f.segment == "rth"].assign(ts=lambda x: pd.to_datetime(x.ts, utc=True))
                             .sort_values("ts").reset_index(drop=True)) if len(f) else f
            if len(self.cache) > 400:                    # bounded memory
                self.cache.pop(next(iter(self.cache)))
        return self.cache[k]


def fade_events() -> pd.DataFrame:
    e = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv")[["symbol", "session", "alert_type"]] for n in
                   ("dev", "holdout")])
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "entry_ts", "cost_rt"]]
    e = e.merge(src, on=["symbol", "session"]).dropna(subset=["entry_ts"])
    return e.assign(session=e.session.astype(str), t0=pd.to_datetime(e.entry_ts, utc=True))


def halt_events(bars: Bars) -> pd.DataFrame:
    h = pd.read_csv("reports/fade_halt_audit/halt_events.csv")
    rows = []
    for r in h.itertuples():
        f = bars(r.symbol, str(r.session))
        last = pd.Timestamp(r.entry_ts)
        nxt = f[f.ts > last]
        if nxt.empty or nxt.ts.iloc[0] - last > REOPEN_MAX:
            continue
        rows.append({"symbol": r.symbol, "session": str(r.session), "t0": nxt.ts.iloc[0], "cost_rt": r.cost_rt})
    return pd.DataFrame(rows)


VARIANTS = {"main": {}, "target3": {"target": 0.03}, "target8": {"target": 0.08}, "hold30": {"hold": 30}}


def score_all(ev: pd.DataFrame, bars: Bars) -> pd.DataFrame:
    """Net per event for every variant, one bar read per event."""
    rows = []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        rows.append({k: long_trade(f, r.t0, **kw) for k, kw in VARIANTS.items()})
    g = pd.DataFrame(rows, index=ev.index, dtype=float)
    return g.sub(ev.cost_rt, axis=0)


def gate(ev: pd.DataFrame, net: pd.Series, min_n: int) -> dict:
    d = ev.assign(net=net).dropna(subset=["net"])
    if d.empty:
        return {"n": 0, "pass": False}
    u, inv = np.unique(d.session.to_numpy(), return_inverse=True)
    s, n = np.bincount(inv, d.net.to_numpy(float), len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    boot = s[b].sum(1) / n[b].sum(1)
    r = {"n": len(d), "mean": float(d.net.mean()), "median": float(d.net.median()), "win": float((d.net > 0).mean()),
         "lower": float(np.quantile(boot, ALPHA)), "stopped": float((d.net <= -STOP).mean())}
    r["pass"] = bool(r["n"] >= min_n and r["lower"] > 0)
    return r


def main() -> int:
    bars = Bars()
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True)   # cache-friendly order
    fe["sigma"] = [sigma(bars.store, r.symbol, r.session, r.t0) for r in fe.itertuples()]
    fams = {"L1_all": (fe, 100), "L2_calm": (fe[fe.sigma < CALM], 100), "L3_reopen": (halt_events(bars), 60)}
    out = {}
    for name, (ev, min_n) in fams.items():
        out[name] = {}
        for split, part in (("train", ev[ev.session < D_STAR]), ("holdout", ev[ev.session >= D_STAR])):
            nets = score_all(part, bars)
            r = gate(part, nets["main"], min_n)
            r["secondary"] = {k: float(nets[k].mean()) for k in ("target3", "target8", "hold30")}
            out[name][split] = r
            tag = " (decides)" if split == "train" else " (not independent)" if name != "L1_all" else ""
            print(f"{name:10s} {split:7s} n{r['n']} mean {r.get('mean', 0):+.2%} median {r.get('median', 0):+.2%} "
                  f"win {r.get('win', 0):.0%} stopped {r.get('stopped', 0):.0%} lower {r.get('lower', 0):+.2%} -> "
                  f"{'PASS' if r['pass'] else 'fail'}{tag} | t3 {r['secondary']['target3']:+.2%} "
                  f"t8 {r['secondary']['target8']:+.2%} 30m {r['secondary']['hold30']:+.2%}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="long-family-2026-11-09-dev", kind="long-family-dev",
                                  params={"prereg": "docs/findings/prereg_long_family_2026-11-09.md"}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
