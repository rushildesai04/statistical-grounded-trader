"""Buy BIG sudden drops, decided once on the sealed 2026 months (prereg_big_flush_2026-11-10; vault big-flush-2026-11).

    python scripts/big_flush_study.py holdout     # ONE-SHOT: vault unlock, 2026-01-02 .. 2026-10-16
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from flush_long_study import long_exit
from self_fade_study import symbol_days
from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.crypto import leadlag
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision.features import CostModelAdapter
from sgt.fast import spikes
from sgt.intraday.store import ParquetBarStore
from sgt.research.vault import Vault, load_registry

STUDY, PREREG, OUT = "big-flush-2026-11", "docs/findings/prereg_big_flush_2026-11-10.md", Path("reports/big_flush")
SEED, RUN, MIN_N, MID = 20261112, 0.10, 50, pd.Timestamp("2026-06-01", tz="UTC")


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
        t = spikes.detect_drop(rth, run=RUN)
        if t is None:
            continue
        entry_ts, entry = rth.ts.iloc[t + 1], float(rth.open.iloc[t + 1])
        ex = long_exit(rth, entry_ts, entry) if entry > 0 else None
        if ex is None:
            continue
        rt = cost.round_trip(sym, entry_ts.to_pydatetime(), entry)[0]
        ex15 = long_exit(rth, entry_ts, entry, hold=15)
        rows.append({"symbol": sym, "session": d.isoformat(), "ts": entry_ts, "entry": entry,
                     "gross": ex[0] / entry - 1, "net": ex[0] / entry - 1 - rt, "how": ex[1],
                     "net15": None if ex15 is None else ex15[0] / entry - 1 - rt})
    return pd.DataFrame(rows)


def main() -> int:
    head = Vault(STUDY).unlock(scorer="scripts/big_flush_study.py")
    print(f"vault {STUDY} unlocked at {head}")
    lo, hi = load_registry()[STUDY].holdout
    t = run(lo, hi)
    p = t.sort_values("ts").reset_index(drop=True) if len(t) else t
    g = leadlag.gate(p, MID, np.random.default_rng(SEED)) if len(p) else {"n": 0}
    n = g.get("n", 0)
    g["pass"] = bool(n >= MIN_N and g["mean"] > 0 and g["lower95"] > 0 and min(g["halves"]) > 0
                     and g["top_month_share"] <= 0.5) if n else False
    g["inconclusive"] = n < MIN_N
    if len(p):
        et = p.ts.dt.tz_convert("America/New_York")
        m = et.dt.hour * 60 + et.dt.minute
        win = pd.cut(m, [0, 660, 840, 1440], labels=["pre11", "11-14", "14-16"], right=False)
        g["secondary"] = {"median": float(p.net.median()), "stopped": float((p.how == "stop").mean()),
                          "hold15": float(p.net15.mean()),
                          "by_window": {str(k): {"n": len(x), "mean": float(x.net.mean())}
                                        for k, x in p.groupby(win, observed=True)}}
    print(json.dumps(g, indent=1, default=str))
    print("VERDICT:", "PASS" if g["pass"] else ("INCONCLUSIVE (n < 50)" if g["inconclusive"] else "FAIL"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "holdout.json").write_text(json.dumps(g, indent=2, default=str))
    t.to_csv(OUT / "holdout_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="big-flush-2026-11-10-holdout", kind="big-flush-holdout",
                                  params={"prereg": PREREG}, metrics=json.loads(json.dumps(g, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
