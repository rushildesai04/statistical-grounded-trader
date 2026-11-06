"""Breakeven exit X-BE (prereg_fade_confirm_2026-11-06, addendum B; the owner's review of RAYA).

    python scripts/fade_breakeven_study.py     # run once: training gate + held-out secondary
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from alert_fade_test import score as research_score
from fade_challengers import _rth, events
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import confirm
from sgt.fast.exits import CHAMPION, indicators, simulate
from sgt.intraday.store import ParquetBarStore

SEED, N_BOOT, TAIL_PP = 20261106, 10_000, 0.05


def score(ev: pd.DataFrame, store: ParquetBarStore) -> pd.DataFrame:
    rows = []
    for r in ev.itertuples(index=False):
        b = _rth(store, r.symbol, str(r.session))
        ind = indicators(b.reset_index(drop=True)) if not b.empty else b
        ts, px, cost = pd.Timestamp(r.entry_ts), float(r.entry), float(r.cost_rt) + LOCATE
        ch = simulate(ind, ts, px, CHAMPION) if not ind.empty else None
        be = confirm.breakeven_gross(ind, ts, px) if not ind.empty else None
        rows.append({"session": str(r.session), "c": np.nan if ch is None else ch["gross"] - cost,
                     "b": np.nan if be is None else be[0] - cost, "early": bool(be and be[1])})
    return pd.DataFrame(rows)


def evaluate(t: pd.DataFrame) -> dict:
    t = t.dropna(subset=["c"])
    d = t.groupby("session")[["c", "b"]].sum()
    diff = (d.b - d.c).to_numpy(float)
    means = diff[np.random.default_rng(SEED).integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1)
    e = t[t.early]
    res = {"n": len(t), "champ": float(t.c.mean()), "be": float(t.b.mean()), "p01_c": float(np.quantile(t.c, .01)),
           "p01_b": float(np.quantile(t.b, .01)), "early_share": float(t.early.mean()),
           "early_be": float(e.b.mean()), "early_if_held": float(e.c.mean()), "diff": float(diff.mean()),
           "lower95": float(np.quantile(means, 0.05))}
    res["pass"] = bool(res["lower95"] > 0 and res["p01_b"] >= res["p01_c"] - TAIL_PP)
    return res


def main() -> int:
    store = ParquetBarStore("data/alpaca")
    out = {}
    for split in ("train", "holdout"):
        ev = events(split)
        t = score(ev, store)
        if split == "train":
            ref = research_score(ev, store).net.to_numpy(float)
            if not np.allclose(t.c.to_numpy(float), ref, equal_nan=True, rtol=0, atol=1e-12):
                raise SystemExit("champion does not reproduce alert_fade_test.score: nothing computed")
        out[split] = evaluate(t)
        print(split, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out[split].items()})
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-breakeven-2026-11-06-dev", kind="fade-breakeven-dev",
                                  params={"prereg": "docs/findings/prereg_fade_confirm_2026-11-06.md#addendum-b"},
                                  metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
