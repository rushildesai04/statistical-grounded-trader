"""Fade spikes we detect ourselves (prereg_self_fade_2026-11-06; vault self-fade-2026-11).

    python scripts/self_fade_study.py dev        # training 2024-10-01 .. 2025-12-31 (run ONCE)
    python scripts/self_fade_study.py holdout    # ONE-SHOT after a dev PASS: vault unlock, 2026-01-02 .. 2026-10-16

Streams one symbol-day at a time (bounded memory on the owner's laptop).
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.crypto import leadlag
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision.features import CostModelAdapter
from sgt.fast import spikes
from sgt.fast.exits import CHAMPION, indicators, simulate
from sgt.intraday.store import ParquetBarStore
from sgt.research.vault import Vault, load_registry

STUDY = "self-fade-2026-11"
PREREG = "docs/findings/prereg_self_fade_2026-11-06.md"
OUT = Path("reports/self_fade")
GAPPERS = Path("data/universe/gappers_2y.csv")
CHANNEL = Path("reports/telegram/trades_pooled_first.csv")
SEED = 20261106
VARIANTS = {"primary": {}, "run3": {"run": 0.03}, "run10": {"run": 0.10}, "vol3": {"vol_mult": 3.0},
            "vol10": {"vol_mult": 10.0}, "no_hod": {"need_hod": False}}


def symbol_days(lo: date, hi: date) -> list[tuple[str, date]]:
    out = []
    with GAPPERS.open() as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["session"])
            if lo <= d <= hi and float(r["prev_volume"] or 0) >= 50_000:
                out.append((r["symbol"].upper(), d))
    return sorted(set(out))


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
        ind = None
        for name, kw in VARIANTS.items():
            t = spikes.detect(rth, **kw)
            if t is None:
                continue
            ind = ind if ind is not None else indicators(rth)
            entry_ts, entry = rth.ts.iloc[t + 1], float(rth.open.iloc[t + 1])
            s = simulate(ind, entry_ts, entry, CHAMPION)
            if s is None or entry <= 0:
                continue
            rt = cost.round_trip(sym, entry_ts.to_pydatetime(), entry)[0] + LOCATE
            rows.append({"variant": name, "symbol": sym, "session": d.isoformat(), "ts": entry_ts,
                         "entry": entry, "gross": s["gross"], "net": s["gross"] - rt, "how": s["how"]})
    return pd.DataFrame(rows)


def overlap(t: pd.DataFrame) -> pd.Series:
    """True when a channel alert on the same symbol-day was posted within 5 minutes of the spike entry."""
    ch = pd.read_csv(CHANNEL, usecols=["symbol", "session", "posted_at"])
    ch["posted_at"] = pd.to_datetime(ch.posted_at, utc=True)
    by = {k: g.posted_at.tolist() for k, g in ch.groupby(["symbol", "session"])}
    window = pd.Timedelta(minutes=5)
    return pd.Series([any(abs(p - r.ts) <= window for p in by.get((r.symbol, r.session), []))
                      for r in t.itertuples()], index=t.index)


def report(name: str, t: pd.DataFrame, mid: pd.Timestamp, min_n: int) -> dict:
    p = t[t.variant == "primary"].sort_values("ts").reset_index(drop=True)
    g = leadlag.gate(p, mid, np.random.default_rng(SEED)) if len(p) else {"n": 0, "pass": False}
    if len(p) and min_n < leadlag.MIN_EVENTS and g["n"] >= min_n and not g["pass"]:   # held-out min n is 100
        g["pass"] = bool(g["mean"] > 0 and g["lower95"] > 0 and min(g["halves"]) > 0 and g["top_month_share"] <= 0.5)
    sec = {v: {"n": int((t.variant == v).sum()), "mean": float(t.net[t.variant == v].mean())}
           for v in VARIANTS if v != "primary" and (t.variant == v).any()}
    if len(p):
        et = p.ts.dt.tz_convert("America/New_York")
        m = et.dt.hour * 60 + et.dt.minute
        win = pd.cut(m, [0, 660, 840, 1440], labels=["pre11", "11-14", "14-16"], right=False)
        sec["by_window"] = {str(k): {"n": len(x), "mean": float(x.net.mean())} for k, x in p.groupby(win, observed=True)}
        ov = overlap(p)
        sec["channel_overlap"] = {"share": float(ov.mean()), "mean_overlap": float(p.net[ov].mean()) if ov.any() else None,
                                  "mean_other": float(p.net[~ov].mean()) if (~ov).any() else None}
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
        trial_ledger.record_trial(s, run_id=f"self-fade-2026-11-06-{kind}", kind=f"self-fade-{kind}",
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
    head = Vault(STUDY).unlock(scorer="scripts/self_fade_study.py")
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
