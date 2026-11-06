"""X-REALERT: cover the fade when the channel re-alerts the same stock during the hold
(prereg_fade_later_alerts_2026-11-06, addendum A).

    python scripts/fade_realert_study.py      # run once: training gate + held-out secondary
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_forward import CHANNEL, DB
from alert_fade_test import LOCATE
from alert_fade_test import score as research_score
from fade_challengers import TAIL_PP, _rth, events
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast.exits import CHAMPION, indicators, simulate
from sgt.fast.fade_rule import STOP
from sgt.intraday.store import ParquetBarStore
from telegram_alert_test import attach_trades, load_alerts, primary_events

OUT = Path("reports/fade_realert")
SEED, N_BOOT, REACTION = 20261106, 10_000, pd.Timedelta(seconds=10)


def realert_gross(ind: pd.DataFrame, entry_ts: pd.Timestamp, entry: float, alerts: list[pd.Timestamp]) -> tuple[float, bool]:
    """Champion unless a re-alert lands inside the hold: cover at the open of the first bar >= alert + 10 s, provided
    that bar is not after the champion's own exit bar and the 20% stop was not hit before it."""
    ch = simulate(ind, entry_ts, entry, CHAMPION)
    i0 = int(np.flatnonzero((ind.ts >= entry_ts).to_numpy())[0])
    exit_i = i0 + ch["minutes"] - 1
    hi, op = ind.high.to_numpy(float), ind.open.to_numpy(float)
    for a in sorted(x for x in alerts if x > entry_ts):
        j = np.flatnonzero((ind.ts >= a + REACTION).to_numpy())
        if not len(j) or int(j[0]) > exit_i:
            break                                      # the re-alert comes after the champion is already out
        k = int(j[0])                                  # k > i0 because the alert is after the entry
        if hi[i0:k].max() >= entry * (1 + STOP):
            break                                      # stopped before the reaction bar
        return (entry - op[k]) / entry, True
    return ch["gross"], False


def run(split: str, store: ParquetBarStore, later: dict) -> pd.DataFrame:
    ev = events(split)
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, str(r.session))
        if bars.empty:
            rows.append({"session": str(r.session), "c": np.nan, "x": np.nan, "re": False})
            continue
        ind = indicators(bars.reset_index(drop=True))
        ts, px, cost = pd.Timestamp(r.entry_ts), float(r.entry), float(r.cost_rt) + LOCATE
        ch = simulate(ind, ts, px, CHAMPION)
        if ch is None:
            rows.append({"session": str(r.session), "c": np.nan, "x": np.nan, "re": False})
            continue
        g, re = realert_gross(ind, ts, px, later.get((r.symbol, str(r.session)), []))
        rows.append({"session": str(r.session), "c": ch["gross"] - cost, "x": g - cost, "re": re})
    return pd.DataFrame(rows), ev


def evaluate(t: pd.DataFrame) -> dict:
    t = t.dropna(subset=["c"])
    d = t.groupby("session")[["c", "x"]].sum()
    diff = (d.x - d.c).to_numpy(float)
    means = diff[np.random.default_rng(SEED).integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1)
    re = t[t.re]
    res = {"n": len(t), "champ": float(t.c.mean()), "realert": float(t.x.mean()), "diff": float(diff.mean()),
           "lower95": float(np.quantile(means, 0.05)), "p01_c": float(np.quantile(t.c, .01)),
           "p01_x": float(np.quantile(t.x, .01)), "share_realerted": float(t.re.mean()),
           "realerted_covered": float(re.x.mean()) if len(re) else None,
           "realerted_if_held": float(re.c.mean()) if len(re) else None}
    res["pass"] = bool(res["lower95"] > 0 and res["p01_x"] >= res["p01_c"] - TAIL_PP)
    return res


def main() -> int:
    store = ParquetBarStore("data/alpaca")
    prim = primary_events(attach_trades(load_alerts(DB, CHANNEL), store))
    later: dict = {}
    for (sym, sess), g in prim.groupby(["symbol", prim.session.astype(str)]):
        later[(sym, sess)] = list(pd.to_datetime(g.posted_at, utc=True).sort_values())[1:]   # all but the first
    out = {}
    for split in ("train", "holdout"):
        t, ev = run(split, store, later)
        if split == "train":
            ref = research_score(ev, store).net.to_numpy(float)
            if not np.allclose(t.c.to_numpy(float), ref, equal_nan=True, rtol=0, atol=1e-12):
                raise SystemExit("champion does not reproduce alert_fade_test.score: nothing computed")
        out[split] = evaluate(t)
        print(split, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out[split].items()})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-realert-2026-11-06-dev", kind="fade-realert-dev",
                                  params={"prereg": "docs/findings/prereg_fade_later_alerts_2026-11-06.md#addendum-a"},
                                  metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
