"""Score the pre-registered fade of MetaTradings alerts on the held-out period ONCE.

Rules: docs/findings/prereg_alert_fade_2026-11-01.md. Events, entries and costs come from the alert
test's output (reports/telegram/trades_*.csv) so both tests use identical events.

Usage:
    python scripts/alert_fade_test.py --dry-run     # counts and bar coverage only
    python scripts/alert_fade_test.py
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.core.secrets import get_secret
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore
from telegram_alert_test import boot_means, top5_share

SRC = Path("reports/telegram")
OUT = Path("reports/alert_fade")
STOP = 0.20
HOLD_MIN = 60
LOCATE = 0.005
SEED = 20261101
MIN_N = 200


def cover(w: pd.DataFrame, entry: float, stop: float | None = STOP) -> float | None:
    """Cover price for a short at ``entry``: first bar with high >= entry x (1 + stop) covers at
    max(level, bar open); otherwise the last bar's close. ``w`` = RTH bars of the holding window."""
    if w.empty:
        return None
    if stop is not None:
        lvl = entry * (1 + stop)
        hit = np.flatnonzero(w.high.to_numpy() >= lvl)
        if len(hit):
            return float(max(lvl, w.open.iloc[hit[0]]))
    return float(w.close.iloc[-1])


def window(store: ParquetBarStore, symbol: str, session: str, entry_ts: pd.Timestamp, minutes: int) -> pd.DataFrame:
    d = date.fromisoformat(session)
    f = store.read_frame(symbol, d, d)
    if f.empty:
        return f
    f["ts"] = pd.to_datetime(f.ts, utc=True)
    return f[(f.ts >= entry_ts) & (f.ts < entry_ts + pd.Timedelta(minutes=minutes)) & (f.segment == "rth")]


def score(ev: pd.DataFrame, store: ParquetBarStore) -> pd.DataFrame:
    rows = []
    for r in ev.itertuples(index=False):
        ts = pd.Timestamp(r.entry_ts)
        w60, w15 = window(store, r.symbol, r.session, ts, HOLD_MIN), window(store, r.symbol, r.session, ts, 15)
        c = cover(w60, r.entry)
        g = (lambda px: None if px is None else (r.entry - px) / r.entry)
        rows.append({"symbol": r.symbol, "session": r.session, "alert_type": r.alert_type, "entry": r.entry,
                     "cost_rt": r.cost_rt, "gross": g(c), "gross_nostop": g(cover(w60, r.entry, None)),
                     "gross_15": g(cover(w15, r.entry))})
    t = pd.DataFrame(rows)
    t["net"] = t.gross - t.cost_rt - LOCATE
    return t


def verdict(t: pd.DataFrame, rng: np.random.Generator) -> dict:
    x = t.net.to_numpy(float)
    lb = float(np.quantile(boot_means(x, t.session.astype(str).to_numpy(), rng), 0.05))
    h = len(t) // 2
    s = t.sort_values("session")
    halves = (float(s.net.iloc[:h].mean()), float(s.net.iloc[h:].mean()))
    t5 = top5_share(x)
    ok = len(x) >= MIN_N and lb > 0 and t5 < 0.5 and min(halves) > 0
    return {"n": len(x), "mean": float(x.mean()), "median": float(np.median(x)), "win": float((x > 0).mean()),
            "lower95": lb, "top5": t5, "halves": halves, "worst": float(x.min()),
            "worst_1pct": float(np.quantile(x, 0.01)), "pass": bool(ok)}


def etb_today(symbols: list[str]) -> dict[str, bool]:
    """Alpaca's CURRENT easy-to-borrow flag (look-ahead; secondary only)."""
    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    out = {}
    with httpx.Client(base_url="https://paper-api.alpaca.markets", headers=h, timeout=10) as c:
        for s in symbols:
            r = c.get(f"/v2/assets/{s}")
            a = r.json() if r.status_code == 200 else {}
            out[s] = bool(a.get("status") == "active" and a.get("easy_to_borrow"))
            time.sleep(0.32)                     # 200 req/min account limit
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    ev = pd.read_csv(SRC / "trades_pooled_first.csv")
    ev = ev[(ev.split == "holdout") & (ev.status == "ok")]
    assert (ev.session >= "2026-07-01").all()
    store = ParquetBarStore("data/alpaca")
    t = score(ev, store)
    missing = float(t.gross.isna().mean())
    print(f"held-out events {len(ev)} ({ev.session.min()} .. {ev.session.max()}), no bars {missing:.1%}")
    if args.dry_run:
        return 0
    if missing > 0.10:
        print("VERDICT: INVALID (data)")
        return 1
    t = t.dropna(subset=["gross"])
    rng = np.random.default_rng(SEED)
    res = {"primary": verdict(t, rng)}
    sec = {"no_stop": t.gross_nostop - t.cost_rt - LOCATE, "exit_15m": t.gross_15 - t.cost_rt - LOCATE,
           "locate_1pct": t.gross - t.cost_rt - 0.01, "locate_2pct": t.gross - t.cost_rt - 0.02,
           "gross": t.gross}
    res["secondary"] = {k: {"mean": float(v.mean()), "median": float(v.median()), "win": float((v > 0).mean()),
                            "worst": float(v.min())} for k, v in sec.items()}
    cells = {}
    for f in sorted(SRC.glob("trades_*.csv")):
        c = pd.read_csv(f)
        c = c[(c.split == "holdout") & (c.status == "ok")]
        if f.stem == "trades_pooled_first" or len(c) < 30:
            continue
        ct = score(c, store).dropna(subset=["gross"])
        cells[f.stem[7:]] = {"n": len(ct), "mean": float(ct.net.mean()), "win": float((ct.net > 0).mean())}
    res["cells"] = cells
    etb = etb_today(sorted(t.symbol.unique()))
    e = t[t.symbol.map(etb)]
    res["etb_today"] = {"symbols": int(sum(etb.values())), "of": len(etb), "n": len(e),
                        "mean": float(e.net.mean()) if len(e) else None,
                        "win": float((e.net > 0).mean()) if len(e) else None}
    p = res["primary"]
    print(f"\nPRIMARY n={p['n']} mean net {p['mean']:+.2%} median {p['median']:+.2%} win {p['win']:.0%} "
          f"lower95 {p['lower95']:+.2%} top5 {p['top5']:.2f} halves {p['halves'][0]:+.2%}/{p['halves'][1]:+.2%} "
          f"worst {p['worst']:+.1%} worst1% {p['worst_1pct']:+.1%}")
    print(f"VERDICT: {'PASS' if p['pass'] else 'FAIL'}\n")
    for k, v in res["secondary"].items():
        print(f"{k:12s} mean {v['mean']:+.2%} median {v['median']:+.2%} win {v['win']:.0%} worst {v['worst']:+.1%}")
    for k, v in cells.items():
        print(f"cell {k:18s} n={v['n']:4d} mean {v['mean']:+.2%} win {v['win']:.0%}")
    print("ETB today:", res["etb_today"])
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "trades.csv", index=False)
    (OUT / "result.json").write_text(json.dumps(res, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="alert-fade-2026-11-01", kind="alert-fade",
                                  params={"prereg": "docs/findings/prereg_alert_fade_2026-11-01.md"},
                                  metrics=json.loads(json.dumps(res, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
