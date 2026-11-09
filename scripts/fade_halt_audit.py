"""Halts right after the alert: what the backtest drops and the live book trades (prereg_fade_halt_audit_2026-11-09).

    python scripts/fade_halt_audit.py      # run once: both periods, reported together
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alert_fade_forward import CHANNEL, DB  # noqa: E402
from alert_fade_test import HOLD_MIN, LOCATE, STOP, cover  # noqa: E402
from telegram_alert_test import CELL_OF, ET, REACTION, attach_trades, load_alerts  # noqa: E402

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT  # noqa: E402
from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.decision.features import CostModelAdapter  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/fade_halt_audit")


def live_trade(bars: pd.DataFrame, posted: pd.Timestamp) -> dict | None:
    """Live-style short: the close of the bar containing alert + 10 s, then the champion's exit on later bars."""
    at = posted + REACTION
    b0 = bars[bars.ts == at.floor("min")]
    if b0.empty:
        return None
    t0, entry = b0.ts.iloc[0], float(b0.close.iloc[0])
    w = bars[(bars.ts > t0) & (bars.ts < t0 + pd.Timedelta(minutes=1 + HOLD_MIN))]
    if w.empty:
        return None
    c = cover(w, entry)
    gap = (w.ts.iloc[0] - t0).total_seconds() / 60 - 1               # minutes with no prints right after
    return {"entry_ts": t0, "entry": entry, "cover": c, "gross": (entry - c) / entry,
            "stopped": bool((w.high.to_numpy() >= entry * (1 + STOP)).any()), "gap_min": gap}


def halt_events(t: pd.DataFrame, store: ParquetBarStore) -> pd.DataFrame:
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    et = t.posted_at.dt.tz_convert(ET)
    pm = et.dt.hour * 60 + et.dt.minute
    c = t[t.alert_type.map(CELL_OF).notna() & (pm >= 9 * 60 + 30) & (pm <= 15 * 60 + 43) & (t.status == "no_entry")]
    rows, cache = [], {}
    for r in c.itertuples(index=False):
        key = (r.symbol, r.session)
        if key not in cache:
            f = store.read_frame(r.symbol, r.session, r.session)
            cache[key] = f[f.segment == "rth"].assign(ts=lambda x: pd.to_datetime(x.ts, utc=True)).sort_values("ts") \
                if len(f) else f
        f = cache[key]
        if f.empty:
            continue
        x = live_trade(f, r.posted_at)
        if x is None or not 1 <= x["entry"] <= 50:
            continue
        rt = cost.round_trip(r.symbol, x["entry_ts"].to_pydatetime(), x["entry"])[0]
        rows.append({"symbol": r.symbol, "session": str(r.session), "alert_type": r.alert_type, "posted_at": r.posted_at,
                     **x, "cost_rt": rt, "net": x["gross"] - rt - LOCATE, "kind": "halt"})
    return pd.DataFrame(rows)


def ok_events() -> pd.DataFrame:
    e = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv")[["symbol", "session", "net"]] for n in ("dev", "holdout")])
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "posted_at", "split"]]
    e = e.merge(src, on=["symbol", "session"], how="inner")
    e["posted_at"] = pd.to_datetime(e.posted_at, utc=True)
    return e.assign(session=e.session.astype(str), kind="ok")


def summary(x: pd.DataFrame) -> dict:
    return {"n": len(x), "mean": float(x.net.mean()) if len(x) else None,
            "median": float(x.net.median()) if len(x) else None, "win": float((x.net > 0).mean()) if len(x) else None,
            "stop_rate": float((x.net <= -STOP).mean()) if len(x) else None}


def main() -> int:
    store = ParquetBarStore("data/alpaca")
    t = attach_trades(load_alerts(DB, CHANNEL), store)
    ok = ok_events()
    d_star = ok[ok.split == "holdout"].session.min()
    h = halt_events(t, store)
    h = h.sort_values("posted_at").drop_duplicates(["symbol", "session"])          # first halt event per symbol-day
    h["split"] = np.where(h.session < d_star, "train", "holdout")
    live = pd.concat([ok, h[["symbol", "session", "posted_at", "net", "split", "kind"]]]).sort_values("posted_at")
    live = live.drop_duplicates(["symbol", "session"], keep="first")              # live: the earliest event trades
    out = {"d_star": d_star}
    for split in ("train", "holdout"):
        o, hh, lv = ok[ok.split == split], h[h.split == split], live[live.split == split]
        r = {"ok": summary(o), "halt": summary(hh), "live_semantics": summary(lv),
             "halt_share": len(hh) / max(len(o), 1), "replaced_ok": int(((lv.kind == "halt")).sum()),
             "halt_gap_median_min": float(hh.gap_min.median()) if len(hh) else None}
        out[split] = r
        print(f"{split:8s} ok n{r['ok']['n']} {r['ok']['mean']:+.2%} | halt n{r['halt']['n']} "
              f"({r['halt_share']:.1%}) mean {r['halt']['mean'] or 0:+.2%} median {r['halt']['median'] or 0:+.2%} "
              f"stops {r['halt']['stop_rate'] or 0:.0%} gap {r['halt_gap_median_min']} min | live-semantics "
              f"n{r['live_semantics']['n']} {r['live_semantics']['mean']:+.2%}")
    OUT.mkdir(parents=True, exist_ok=True)
    h.to_csv(OUT / "halt_events.csv", index=False)
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-halt-audit-2026-11-09", kind="fade-halt-audit",
                                  params={"prereg": "docs/findings/prereg_fade_halt_audit_2026-11-09.md"},
                                  metrics=json.loads(json.dumps(out, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
