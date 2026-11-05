"""Fade exits that read the tape (prereg_fade_exits_2026-11-05): trail, VWAP reclaim, EMA 9/21 cross, target.

    python scripts/fade_exits.py dev      # training events: paired gate vs the champion (run ONCE)
    python scripts/fade_exits.py shadow   # nightly (runner, after the forward record): forward events

Same events, entries and costs as the frozen rule; the champion computed here equals alert_fade_test.score.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from fade_challengers import ET, N_BOOT, TAIL_PP, _rth, events
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast.exits import CHAMPION, EXITS, indicators, simulate
from sgt.intraday.store import ParquetBarStore

PREREG = "docs/findings/prereg_fade_exits_2026-11-05.md"
OUT = Path("reports/fade_exits")
SEED = 20261105
ALPHA = 0.05 / len(EXITS)


def score(ev: pd.DataFrame, store: ParquetBarStore) -> pd.DataFrame:
    """One row per event: ``net_<id>``, ``how_<id>``, ``min_<id>`` per exit (NaN net = no trade)."""
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, str(r.session))
        ind = indicators(bars) if not bars.empty else bars
        row = {"symbol": r.symbol, "session": str(r.session), "alert_type": r.alert_type}
        for x in (CHAMPION, *EXITS):
            s = simulate(ind, pd.Timestamp(r.entry_ts), float(r.entry), x) if not ind.empty else None
            row[f"net_{x.id}"] = np.nan if s is None else s["gross"] - r.cost_rt - LOCATE
            row[f"how_{x.id}"] = None if s is None else s["how"]
            row[f"min_{x.id}"] = np.nan if s is None else s["minutes"]
        rows.append(row)
    return pd.DataFrame(rows)


def paired(t: pd.DataFrame, xid: str, rng) -> dict:
    """Paired per-session difference (exit - champion) of total net; bootstrap over sessions. Only events both
    legs traded are compared (review 2026-11-05 F-6: a NaN leg summed as 0 would credit the other leg)."""
    both = t.dropna(subset=[f"net_{CHAMPION.id}", f"net_{xid}"])
    daily = both.groupby("session")[[f"net_{CHAMPION.id}", f"net_{xid}"]].sum()
    diff = (daily[f"net_{xid}"] - daily[f"net_{CHAMPION.id}"]).to_numpy(float)
    means = diff[rng.integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(axis=1)
    x, c = t[f"net_{xid}"].dropna(), t[f"net_{CHAMPION.id}"].dropna()
    how = t[f"how_{xid}"].value_counts(normalize=True).round(3).to_dict()
    return {"sessions": len(diff), "mean_daily_diff": float(diff.mean()),
            "lower": float(np.quantile(means, ALPHA)), "upper": float(np.quantile(means, 1 - ALPHA)),
            "n": len(x), "mean": float(x.mean()), "win": float((x > 0).mean()), "p01": float(np.quantile(x, 0.01)),
            "median_min": float(t[f"min_{xid}"].median()), "how": how,
            "champ_mean": float(c.mean()), "champ_win": float((c > 0).mean()), "champ_p01": float(np.quantile(c, 0.01))}


def evaluate(t: pd.DataFrame) -> dict:
    rng = np.random.default_rng(SEED)
    res = {}
    for x in EXITS:
        p = paired(t, x.id, rng)
        p["pass"] = bool(p["lower"] > 0 and p["p01"] >= p["champ_p01"] - TAIL_PP)
        res[x.id] = p
    return res


def _print(title: str, res: dict) -> None:
    print(title)
    c = res[EXITS[0].id]
    print(f"  champion  mean {c['champ_mean']:+.2%} win {c['champ_win']:.0%} p01 {c['champ_p01']:+.1%}")
    for x in EXITS:
        p = res[x.id]
        print(f"  {x.id:8s} mean {p['mean']:+.2%} win {p['win']:.0%} p01 {p['p01']:+.1%} held {p['median_min']:.0f}m "
              f"{p['how']} | daily diff {p['mean_daily_diff']:+.3%} [{p['lower']:+.3%}, {p['upper']:+.3%}] -> "
              f"{'PASS' if p['pass'] else 'fail'}")


def cmd_dev() -> int:
    from alert_fade_test import score as research_score

    store = ParquetBarStore("data/alpaca")
    ev = events("train")
    t = score(ev, store)
    ref = research_score(ev, store).net.to_numpy(float)
    if not np.allclose(t[f"net_{CHAMPION.id}"].to_numpy(float), ref, equal_nan=True, rtol=0, atol=1e-12):
        raise SystemExit("champion does not reproduce alert_fade_test.score: nothing computed")
    res = evaluate(t)
    _print(f"DEV training {t.session.min()} .. {t.session.max()}: {len(t)} events", res)
    ho = score(events("holdout"), store)
    sec = evaluate(ho)
    _print(f"SECONDARY held-out {ho.session.min()} .. {ho.session.max()}: {len(ho)} events (already seen once)", sec)
    OUT.mkdir(parents=True, exist_ok=True)
    out = {"dev": res, "holdout_secondary": sec, "passed": [x.id for x in EXITS if res[x.id]["pass"]]}
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    t.to_csv(OUT / "dev_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-exits-2026-11-05-dev", kind="fade-exits-dev",
                                  params={"prereg": PREREG}, metrics=out)
    return 0


def summary_line(t: pd.DataFrame) -> str:
    parts = [f"{x.id} {t[f'net_{x.id}'].mean():+.2%}" for x in (CHAMPION, *EXITS)]
    return f"fade exits fwd n={len(t)}: " + ", ".join(parts)


def cmd_shadow() -> int:
    from alert_fade_forward import CHANNEL, DB, forward_events
    from megacap_forward import last_complete_session
    from telegram_alert_test import load_alerts

    store = ParquetBarStore("data/alpaca")
    end = last_complete_session(datetime.now(ET))
    ev = forward_events(load_alerts(DB, CHANNEL), store, end, fill_bars=False).reset_index(drop=True)
    ev = ev[ev.status == "ok"] if "status" in ev else ev
    if ev.empty:
        print("no forward events yet")
        return 0
    t = score(ev, store).dropna(subset=[f"net_{CHAMPION.id}"])
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "shadow.csv", index=False)
    line = summary_line(t)
    (OUT / "shadow_summary.json").write_text(json.dumps({"asof": str(end), "n": len(t), "line": line}, indent=2))
    print(line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "shadow"])
    return {"dev": cmd_dev, "shadow": cmd_shadow}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
