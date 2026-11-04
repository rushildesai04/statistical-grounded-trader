"""Fade challengers in shadow (prereg_fade_challengers_2026-11-04): entry delay (D2, D4) and holding time (H30, H90).

    python scripts/fade_challengers.py dev      # training events: paired gate vs the champion (run ONCE)
    python scripts/fade_challengers.py shadow   # nightly (runner, after the forward record): forward events

Same events, cover function and costs as the frozen rule; the champion computed here equals alert_fade_test.score.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alert_fade_test import HOLD_MIN, LOCATE, STOP, cover
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore

ET = ZoneInfo("America/New_York")
SRC = Path("reports/telegram/trades_pooled_first.csv")
OUT = Path("reports/fade_challengers")
SEED, N_BOOT = 20261104, 10_000
TAIL_PP = 0.05                                     # 1st-percentile net may be at most 5 pp worse than the champion
D4_COST = -0.005                                   # diagnostic: D4 mean daily difference below this (in trade units)


@dataclass(frozen=True, slots=True)
class Variant:
    id: str
    delay_min: int = 0
    hold_min: int = HOLD_MIN
    stop: float = STOP


CHAMPION = Variant("champion")
CHALLENGERS = (Variant("D2", delay_min=2), Variant("D4", delay_min=4), Variant("H30", hold_min=30),
               Variant("H90", hold_min=90))
ALPHA = 0.05 / len(CHALLENGERS)


def _rth(store: ParquetBarStore, symbol: str, session: str) -> pd.DataFrame:
    d = date.fromisoformat(session)
    f = store.read_frame(symbol, d, d)
    if f.empty:
        return f
    f = f.assign(ts=pd.to_datetime(f.ts, utc=True))
    return f[f.segment == "rth"].sort_values("ts")


def gross(bars: pd.DataFrame, entry_ts: pd.Timestamp, entry: float, v: Variant) -> float | None:
    """Gross return of the short under variant ``v``; None = no trade (no RTH bar to enter or hold)."""
    if bars.empty:
        return None
    if v.delay_min:
        later = bars[bars.ts >= entry_ts + pd.Timedelta(minutes=v.delay_min)]
        if later.empty:
            return None
        entry_ts, entry = later.ts.iloc[0], float(later.open.iloc[0])
    w = bars[(bars.ts >= entry_ts) & (bars.ts < entry_ts + pd.Timedelta(minutes=v.hold_min))]
    c = cover(w, entry, v.stop)
    return None if c is None else (entry - c) / entry


def score(ev: pd.DataFrame, store: ParquetBarStore, variants=(CHAMPION, *CHALLENGERS)) -> pd.DataFrame:
    """One row per event: ``net_<id>`` per variant (NaN = no trade)."""
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, str(r.session))
        ts = pd.Timestamp(r.entry_ts)
        row = {"symbol": r.symbol, "session": str(r.session), "alert_type": r.alert_type}
        for v in variants:
            g = gross(bars, ts, float(r.entry), v)
            row[f"net_{v.id}"] = np.nan if g is None else g - r.cost_rt - LOCATE
        rows.append(row)
    return pd.DataFrame(rows)


def paired(t: pd.DataFrame, cid: str, rng) -> dict:
    """Paired per-session difference (challenger - champion) of total net; bootstrap over sessions."""
    daily = t.groupby("session")[[f"net_{CHAMPION.id}", f"net_{cid}"]].sum(min_count=0)
    diff = (daily[f"net_{cid}"] - daily[f"net_{CHAMPION.id}"]).to_numpy(float)
    idx = rng.integers(0, len(diff), size=(N_BOOT, len(diff)))
    means = diff[idx].mean(axis=1)
    x, c = t[f"net_{cid}"].dropna(), t[f"net_{CHAMPION.id}"].dropna()
    return {"sessions": len(diff), "mean_daily_diff": float(diff.mean()),
            "lower": float(np.quantile(means, ALPHA)), "upper": float(np.quantile(means, 1 - ALPHA)),
            "n": len(x), "mean": float(x.mean()), "win": float((x > 0).mean()),
            "p01": float(np.quantile(x, 0.01)), "champ_mean": float(c.mean()), "champ_p01": float(np.quantile(c, 0.01))}


def evaluate(t: pd.DataFrame) -> dict:
    rng = np.random.default_rng(SEED)
    res = {}
    for v in CHALLENGERS:
        p = paired(t, v.id, rng)
        p["pass"] = bool(p["lower"] > 0 and p["p01"] >= p["champ_p01"] - TAIL_PP)
        res[v.id] = p
    d4 = res["D4"]
    res["d4_costs_money"] = bool(d4["mean_daily_diff"] < D4_COST and d4["upper"] < 0)
    return res


def events(split: str) -> pd.DataFrame:
    ev = pd.read_csv(SRC)
    return ev[(ev.split == split) & (ev.status == "ok")].reset_index(drop=True)


def _print(title: str, res: dict) -> None:
    print(title)
    for v in CHALLENGERS:
        p = res[v.id]
        print(f"  {v.id:4s} n {p['n']:4d} mean {p['mean']:+.2%} win {p['win']:.0%} p01 {p['p01']:+.1%} | daily diff "
              f"{p['mean_daily_diff']:+.3%} [{p['lower']:+.3%}, {p['upper']:+.3%}] -> {'PASS' if p['pass'] else 'fail'}")
    c = res[CHALLENGERS[0].id]
    print(f"  champion mean {c['champ_mean']:+.2%} p01 {c['champ_p01']:+.1%}; D4 costs money: {res['d4_costs_money']}")


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
    out = {"dev": res, "holdout_secondary": sec, "passed": [v.id for v in CHALLENGERS if res[v.id]["pass"]]}
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-challengers-2026-11-04-dev", kind="fade-challengers-dev",
                                  params={"prereg": "docs/findings/prereg_fade_challengers_2026-11-04.md"}, metrics=out)
    return 0


def summary_line(t: pd.DataFrame) -> str:
    """Digest line: forward mean net per trade, champion vs each challenger."""
    parts = [f"{v.id} {t[f'net_{v.id}'].mean():+.2%}" for v in (CHAMPION, *CHALLENGERS)]
    return f"fade challengers fwd n={len(t)}: " + ", ".join(parts)


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
