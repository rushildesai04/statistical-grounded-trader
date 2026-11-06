"""Confirmation entry and failed-pullback exit (prereg_fade_confirm_2026-11-06; RAYA review).

    python scripts/fade_confirm_study.py dev     # training gate (run ONCE); held-out secondary
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from fade_challengers import TAIL_PP, _rth, events
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import confirm
from sgt.fast.exits import CHAMPION, indicators, simulate
from sgt.intraday.store import ParquetBarStore

PREREG = "docs/findings/prereg_fade_confirm_2026-11-06.md"
OUT = Path("reports/fade_confirm")
SEED, N_BOOT, ALPHA = 20261106, 10_000, 0.05 / 2


def score(ev: pd.DataFrame, store: ParquetBarStore) -> pd.DataFrame:
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, str(r.session))
        ind = indicators(bars.reset_index(drop=True)) if not bars.empty else bars
        ts, px, cost = pd.Timestamp(r.entry_ts), float(r.entry), float(r.cost_rt) + LOCATE
        ch = simulate(ind, ts, px, CHAMPION) if not ind.empty else None
        ce = confirm.confirm_gross(ind, ts) if not ind.empty else None
        fp = confirm.failpb_gross(ind, ts, px) if not ind.empty else None
        rows.append({"symbol": r.symbol, "session": str(r.session), "alert_type": r.alert_type,
                     "net_champion": np.nan if ch is None else ch["gross"] - cost,
                     "net_confirm": np.nan if ce is None else ce - cost, "confirm_traded": ce is not None,
                     "net_failpb": np.nan if fp is None else fp[0] - cost, "failpb_early": bool(fp and fp[1]),
                     "gross_champion": np.nan if ch is None else ch["gross"]})
    return pd.DataFrame(rows)


def paired(t: pd.DataFrame, col: str, rng) -> dict:
    """Per-session total net vs the champion; an untraded challenger event counts 0 (prereg, as D2/D4)."""
    t = t.dropna(subset=["net_champion"])
    daily = t.assign(x=t[col].fillna(0.0)).groupby("session")[["net_champion", "x"]].sum()
    diff = (daily.x - daily.net_champion).to_numpy(float)
    means = diff[rng.integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1)
    x = t[col].dropna()
    p01_c, p01_x = float(np.quantile(t.net_champion, 0.01)), float(np.quantile(x, 0.01))
    lower = float(np.quantile(means, ALPHA))
    return {"n_traded": len(x), "mean_traded": float(x.mean()), "win": float((x > 0).mean()), "p01": p01_x,
            "champ_mean": float(t.net_champion.mean()), "champ_p01": p01_c, "diff_per_session": float(diff.mean()),
            "lower": lower, "pass": bool(lower > 0 and p01_x >= p01_c - TAIL_PP)}


def evaluate(t: pd.DataFrame) -> dict:
    rng = np.random.default_rng(SEED)
    res = {"E-CONFIRM": paired(t, "net_confirm", rng), "X-FAILPB": paired(t, "net_failpb", rng)}
    res["E-CONFIRM"]["share_traded"] = float(t.confirm_traded.mean())
    e = t[t.failpb_early]
    res["X-FAILPB"]["share_early"] = float(t.failpb_early.mean())
    res["X-FAILPB"]["early_net_vs_champion"] = [float(e.net_failpb.mean()), float(e.net_champion.mean())] if len(e) else None
    return res


def _print(name: str, r: dict) -> None:
    print(name)
    c = r["E-CONFIRM"]
    print(f"  champion {c['champ_mean']:+.2%}/trade p01 {c['champ_p01']:+.1%}")
    print(f"  E-CONFIRM traded {c['share_traded']:.0%}: {c['mean_traded']:+.2%}/traded trade, win {c['win']:.0%}, "
          f"p01 {c['p01']:+.1%} | diff/session {c['diff_per_session']:+.3%} (lower {c['lower']:+.3%}) -> "
          f"{'PASS' if c['pass'] else 'fail'}")
    f = r["X-FAILPB"]
    ev = f["early_net_vs_champion"]
    print(f"  X-FAILPB {f['mean_traded']:+.2%}/trade, p01 {f['p01']:+.1%}, covered early {f['share_early']:.0%}"
          + (f" ({ev[0]:+.2%} vs {ev[1]:+.2%} if held)" if ev else "")
          + f" | diff/session {f['diff_per_session']:+.3%} (lower {f['lower']:+.3%}) -> {'PASS' if f['pass'] else 'fail'}")


def cmd_dev() -> int:
    from alert_fade_test import score as research_score

    store = ParquetBarStore("data/alpaca")
    ev = events("train")
    t = score(ev, store)
    ref = research_score(ev, store).net.to_numpy(float)
    if not np.allclose(t.net_champion.to_numpy(float), ref, equal_nan=True, rtol=0, atol=1e-12):
        raise SystemExit("champion does not reproduce alert_fade_test.score: nothing computed")
    res = evaluate(t)
    _print(f"DEV training {t.session.min()} .. {t.session.max()}: {len(t)} events (decides)", res)
    ho = score(events("holdout"), store)
    sec = evaluate(ho)
    _print(f"SECONDARY held-out {ho.session.min()} .. {ho.session.max()}: {len(ho)} events (already seen)", sec)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "dev_events.csv", index=False)
    ho.to_csv(OUT / "holdout_events.csv", index=False)
    out = {"dev": res, "holdout_secondary": sec}
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-confirm-2026-11-06-dev", kind="fade-confirm-dev",
                                  params={"prereg": PREREG}, metrics=out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev"])
    return {"dev": cmd_dev}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
