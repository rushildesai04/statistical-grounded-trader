"""Buy sudden drops we detect ourselves: the fade's mirror, long (prereg_flush_long_2026-11-10; vault flush-long-2026-11).

    python scripts/flush_long_study.py dev        # training 2024-10-01 .. 2025-12-31 (run ONCE)
    python scripts/flush_long_study.py holdout    # ONE-SHOT after a dev PASS: vault unlock, 2026-01-02 .. 2026-10-16

Streams one symbol-day at a time (bounded memory on the owner's laptop).
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from self_fade_study import symbol_days
from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.crypto import leadlag
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision.features import CostModelAdapter
from sgt.fast import spikes
from sgt.intraday.store import ParquetBarStore
from sgt.research.vault import Vault, load_registry

STUDY = "flush-long-2026-11"
PREREG = "docs/findings/prereg_flush_long_2026-11-10.md"
OUT = Path("reports/flush_long")
SEED, STOP, HOLD = 20261110, 0.20, 60
VARIANTS = {"primary": {}, "run3": {"run": 0.03}, "run10": {"run": 0.10}, "vol3": {"vol_mult": 3.0},
            "vol10": {"vol_mult": 10.0}, "no_lod": {"need_lod": False}}


def long_exit(rth: pd.DataFrame, entry_ts: pd.Timestamp, entry: float, hold: int = HOLD,
              stop: float = STOP) -> tuple[float, str] | None:
    """Exit of a long bought at ``entry`` on the bar starting ``entry_ts``: first bar with low <= entry x (1 - stop)
    sells at min(level, bar open); otherwise the close of the last bar within ``hold`` minutes. The fade's cover()
    mirrored."""
    w = rth[(rth.ts >= entry_ts) & (rth.ts < entry_ts + pd.Timedelta(minutes=hold))]
    if w.empty:
        return None
    lvl = entry * (1 - stop)
    hit = np.flatnonzero(w.low.to_numpy() <= lvl)
    if len(hit):
        return float(min(lvl, w.open.iloc[hit[0]])), "stop"
    return float(w.close.iloc[-1]), "time"


def run(lo: date, hi: date) -> pd.DataFrame:
    store = ParquetBarStore("data/alpaca")
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    rows = []
    for sym, d in symbol_days(lo, hi):
        f = store.read_frame(sym, d, d)
        if f.empty:
            continue
        rth = f[f.segment == "rth"].assign(ts=lambda x: pd.to_datetime(x.ts, utc=True)).sort_values("ts") \
            .reset_index(drop=True)
        if len(rth) < 30:
            continue
        for name, kw in VARIANTS.items():
            t = spikes.detect_drop(rth, **kw)
            if t is None:
                continue
            entry_ts, entry = rth.ts.iloc[t + 1], float(rth.open.iloc[t + 1])
            if entry <= 0:
                continue
            ex = long_exit(rth, entry_ts, entry)
            if ex is None:
                continue
            rt = cost.round_trip(sym, entry_ts.to_pydatetime(), entry)[0]
            ex15 = long_exit(rth, entry_ts, entry, hold=15)
            rows.append({"variant": name, "symbol": sym, "session": d.isoformat(), "ts": entry_ts, "entry": entry,
                         "gross": ex[0] / entry - 1, "net": ex[0] / entry - 1 - rt, "how": ex[1],
                         "net15": None if ex15 is None else ex15[0] / entry - 1 - rt})
    return pd.DataFrame(rows)


def report(name: str, t: pd.DataFrame, mid: pd.Timestamp, min_n: int) -> dict:
    p = t[t.variant == "primary"].sort_values("ts").reset_index(drop=True) if len(t) else t
    g = leadlag.gate(p, mid, np.random.default_rng(SEED)) if len(p) else {"n": 0, "pass": False}
    if len(p) and min_n < leadlag.MIN_EVENTS and g["n"] >= min_n and not g["pass"]:   # held-out min n is 100
        g["pass"] = bool(g["mean"] > 0 and g["lower95"] > 0 and min(g["halves"]) > 0 and g["top_month_share"] <= 0.5)
    sec = {v: {"n": int((t.variant == v).sum()), "mean": float(t.net[t.variant == v].mean())}
           for v in VARIANTS if v != "primary" and len(t) and (t.variant == v).any()}
    if len(p):
        et = p.ts.dt.tz_convert("America/New_York")
        m = et.dt.hour * 60 + et.dt.minute
        win = pd.cut(m, [0, 660, 840, 1440], labels=["pre11", "11-14", "14-16"], right=False)
        sec["by_window"] = {str(k): {"n": len(x), "mean": float(x.net.mean())} for k, x in p.groupby(win, observed=True)}
        sec["hold15"] = float(p.net15.mean())
        sec["stopped"] = float((p.how == "stop").mean())
    print(f"{name}: n {g['n']} mean net {g.get('mean', float('nan')):+.3%} gross {g.get('gross', float('nan')):+.3%} "
          f"hit {g.get('hit', float('nan')):.0%} lower95 {g.get('lower95', float('nan')):+.3%} halves {g.get('halves')} "
          f"top month {g.get('top_month_share')}")
    for k, v in sec.items():
        print(f"  {k}: {v}")
    print(f"VERDICT ({name}): {'PASS' if g['pass'] else 'FAIL'}")
    return {"primary": g, "secondary": sec}


def save(kind: str, res: dict, t: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{kind}.json").write_text(json.dumps(res, indent=2, default=str))
    t.to_csv(OUT / f"{kind}_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id=f"flush-long-2026-11-10-{kind}", kind=f"flush-long-{kind}",
                                  params={"prereg": PREREG}, metrics=json.loads(json.dumps(res, default=str)))


def cmd_dev() -> int:
    lo, hi = load_registry()[STUDY].train
    t = run(lo, hi)
    save("dev", report("DEV training (decides)", t, pd.Timestamp("2025-06-01", tz="UTC"), leadlag.MIN_EVENTS), t)
    return 0


def cmd_holdout() -> int:
    dev = json.loads((OUT / "dev.json").read_text())
    if not dev["primary"].get("pass"):
        raise SystemExit("development gate did not pass: the held-out stays sealed (prereg)")
    head = Vault(STUDY).unlock(scorer="scripts/flush_long_study.py")
    print(f"vault {STUDY} unlocked at {head}")
    lo, hi = load_registry()[STUDY].holdout
    t = run(lo, hi)
    save("holdout", report("HELD-OUT (decisive)", t, pd.Timestamp("2026-06-01", tz="UTC"), 100), t)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "holdout"])
    return {"dev": cmd_dev, "holdout": cmd_holdout}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
