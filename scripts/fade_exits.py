"""Fade exit studies vs the frozen 60-minute champion, each under its own pre-registration.

    python scripts/fade_exits.py dev                  # exits (prereg_fade_exits_2026-11-05): trail, VWAP, EMA 9/21, target
    python scripts/fade_exits.py dev --study hold     # holds (prereg_fade_hold_2026-11-05): 2 hours, to the close
    python scripts/fade_exits.py shadow [--study S]   # nightly (runner, after the forward record): forward events

`dev` is run ONCE per study. Same events, entries and costs as the frozen rule; the champion computed here equals
alert_fade_test.score.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from fade_challengers import ET, N_BOOT, TAIL_PP, _rth, events
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast.exits import CHAMPION, EXITS, HOLDS, Exit, indicators, simulate
from sgt.intraday.store import ParquetBarStore

SEED = 20261105


@dataclass(frozen=True, slots=True)
class Study:
    name: str
    variants: tuple[Exit, ...]
    prereg: str
    out: Path

    @property
    def alpha(self) -> float:
        return 0.05 / len(self.variants)


STUDIES = {"exits": Study("exits", EXITS, "docs/findings/prereg_fade_exits_2026-11-05.md", Path("reports/fade_exits")),
           "hold": Study("hold", HOLDS, "docs/findings/prereg_fade_hold_2026-11-05.md", Path("reports/fade_hold"))}
# kept for callers of the first study
PREREG, OUT, ALPHA = STUDIES["exits"].prereg, STUDIES["exits"].out, STUDIES["exits"].alpha


def score(ev: pd.DataFrame, store: ParquetBarStore, variants: tuple[Exit, ...] = EXITS) -> pd.DataFrame:
    """One row per event: ``net_<id>``, ``how_<id>``, ``min_<id>`` per exit (NaN net = no trade)."""
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, str(r.session))
        ind = indicators(bars) if not bars.empty else bars
        row = {"symbol": r.symbol, "session": str(r.session), "alert_type": r.alert_type,
               "entry_ts": str(r.entry_ts)}
        for x in (CHAMPION, *variants):
            s = simulate(ind, pd.Timestamp(r.entry_ts), float(r.entry), x) if not ind.empty else None
            row[f"net_{x.id}"] = np.nan if s is None else s["gross"] - r.cost_rt - LOCATE
            row[f"how_{x.id}"] = None if s is None else s["how"]
            row[f"min_{x.id}"] = np.nan if s is None else s["minutes"]
        rows.append(row)
    return pd.DataFrame(rows)


def paired(t: pd.DataFrame, xid: str, rng, alpha: float = ALPHA) -> dict:
    """Paired per-session difference (exit - champion) of total net; bootstrap over sessions. Only events both
    legs traded are compared (review 2026-11-05 F-6: a NaN leg summed as 0 would credit the other leg)."""
    both = t.dropna(subset=[f"net_{CHAMPION.id}", f"net_{xid}"])
    daily = both.groupby("session")[[f"net_{CHAMPION.id}", f"net_{xid}"]].sum()
    diff = (daily[f"net_{xid}"] - daily[f"net_{CHAMPION.id}"]).to_numpy(float)
    means = diff[rng.integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(axis=1)
    x, c = t[f"net_{xid}"].dropna(), t[f"net_{CHAMPION.id}"].dropna()
    how = t[f"how_{xid}"].value_counts(normalize=True).round(3).to_dict()
    return {"sessions": len(diff), "mean_daily_diff": float(diff.mean()),
            "lower": float(np.quantile(means, alpha)), "upper": float(np.quantile(means, 1 - alpha)),
            "n": len(x), "mean": float(x.mean()), "win": float((x > 0).mean()), "p01": float(np.quantile(x, 0.01)),
            "median_min": float(t[f"min_{xid}"].median()), "how": how,
            "champ_mean": float(c.mean()), "champ_win": float((c > 0).mean()), "champ_p01": float(np.quantile(c, 0.01))}


def evaluate(t: pd.DataFrame, study: Study = STUDIES["exits"]) -> dict:
    rng = np.random.default_rng(SEED)
    res = {}
    for x in study.variants:
        p = paired(t, x.id, rng, study.alpha)
        p["pass"] = bool(p["lower"] > 0 and p["p01"] >= p["champ_p01"] - TAIL_PP)
        res[x.id] = p
    return res


def by_entry_hour(t: pd.DataFrame, study: Study) -> dict:
    """Secondary: mean net per variant by ET entry window."""
    h = pd.to_datetime(t.entry_ts, utc=True).dt.tz_convert(ET)
    m = h.dt.hour * 60 + h.dt.minute
    win = pd.cut(m, [0, 660, 840, 1440], labels=["09:30-11", "11-14", "14-16"], right=False)
    cols = [f"net_{x.id}" for x in (CHAMPION, *study.variants)]
    return {str(k): {c[4:]: float(v) for c, v in g[cols].mean().items()} | {"n": len(g)}
            for k, g in t.groupby(win, observed=True)}


def _print(title: str, res: dict, study: Study = STUDIES["exits"]) -> None:
    print(title)
    c = res[study.variants[0].id]
    print(f"  champion  mean {c['champ_mean']:+.2%} win {c['champ_win']:.0%} p01 {c['champ_p01']:+.1%}")
    for x in study.variants:
        p = res[x.id]
        print(f"  {x.id:8s} mean {p['mean']:+.2%} win {p['win']:.0%} p01 {p['p01']:+.1%} held {p['median_min']:.0f}m "
              f"{p['how']} | daily diff {p['mean_daily_diff']:+.3%} [{p['lower']:+.3%}, {p['upper']:+.3%}] -> "
              f"{'PASS' if p['pass'] else 'fail'}")


def cmd_dev(study: Study) -> int:
    from alert_fade_test import score as research_score

    store = ParquetBarStore("data/alpaca")
    ev = events("train")
    t = score(ev, store, study.variants)
    ref = research_score(ev, store).net.to_numpy(float)
    if not np.allclose(t[f"net_{CHAMPION.id}"].to_numpy(float), ref, equal_nan=True, rtol=0, atol=1e-12):
        raise SystemExit("champion does not reproduce alert_fade_test.score: nothing computed")
    res = evaluate(t, study)
    _print(f"DEV training {t.session.min()} .. {t.session.max()}: {len(t)} events", res, study)
    ho = score(events("holdout"), store, study.variants)
    sec = evaluate(ho, study)
    _print(f"SECONDARY held-out {ho.session.min()} .. {ho.session.max()}: {len(ho)} events (already seen once)",
           sec, study)
    hours = {"dev": by_entry_hour(t, study), "holdout": by_entry_hour(ho, study)}
    print("by entry hour:", json.dumps(hours, indent=1))
    study.out.mkdir(parents=True, exist_ok=True)
    out = {"dev": res, "holdout_secondary": sec, "by_entry_hour": hours,
           "passed": [x.id for x in study.variants if res[x.id]["pass"]]}
    (study.out / "dev.json").write_text(json.dumps(out, indent=2))
    t.to_csv(study.out / "dev_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id=f"fade-{study.name}-2026-11-05-dev", kind=f"fade-{study.name}-dev",
                                  params={"prereg": study.prereg}, metrics=out)
    return 0


def summary_line(t: pd.DataFrame, study: Study = STUDIES["exits"]) -> str:
    parts = [f"{x.id} {t[f'net_{x.id}'].mean():+.2%}" for x in (CHAMPION, *study.variants)]
    return f"fade {study.name} fwd n={len(t)}: " + ", ".join(parts)


def cmd_shadow(study: Study) -> int:
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
    t = score(ev, store, study.variants).dropna(subset=[f"net_{CHAMPION.id}"])
    study.out.mkdir(parents=True, exist_ok=True)
    t.to_csv(study.out / "shadow.csv", index=False)
    line = summary_line(t, study)
    (study.out / "shadow_summary.json").write_text(json.dumps({"asof": str(end), "n": len(t), "line": line}, indent=2))
    print(line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "shadow"])
    ap.add_argument("--study", choices=sorted(STUDIES), default="exits")
    a = ap.parse_args()
    return {"dev": cmd_dev, "shadow": cmd_shadow}[a.cmd](STUDIES[a.study])


if __name__ == "__main__":
    raise SystemExit(main())
