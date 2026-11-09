"""Real fills of the fade's 20% stop from SIP trades (prereg_fade_stop_slippage_2026-11-09).

    python scripts/fade_stop_slippage.py fetch     # resumable cache reports/fade_stops/fills.csv
    python scripts/fade_stop_slippage.py report
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alert_fade_test import HOLD_MIN, STOP  # noqa: E402
from fade_passive_queue import DROP, _get  # noqa: E402

from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/fade_stops")
CACHE = OUT / "fills.csv"
FIELDS = ["symbol", "session", "split", "entry", "level", "net", "model_cover", "trigger_ts", "trigger_px", "fill", "n"]
WINDOW, MAX_PAGES = pd.Timedelta(seconds=2), 3


def stopped() -> pd.DataFrame:
    e = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv")[["symbol", "session", "net"]].assign(split=n)
                   for n in ("dev", "holdout")])
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "entry_ts", "entry"]]
    e = e.merge(src, on=["symbol", "session"]).dropna(subset=["net", "entry_ts"])
    store, rows = ParquetBarStore("data/alpaca"), []
    for r in e.itertuples():
        d = date.fromisoformat(str(r.session))
        f = store.read_frame(r.symbol, d, d)
        if f.empty:
            continue
        f = f.assign(ts=pd.to_datetime(f.ts, utc=True))
        t0 = pd.Timestamp(r.entry_ts)
        w = f[(f.segment == "rth") & (f.ts >= t0) & (f.ts < t0 + pd.Timedelta(minutes=HOLD_MIN))].sort_values("ts")
        lvl = r.entry * (1 + STOP)
        hit = w[w.high >= lvl]
        if hit.empty:
            continue
        b = hit.iloc[0]
        rows.append({"symbol": r.symbol, "session": str(r.session), "split": r.split, "entry": r.entry, "level": lvl,
                     "net": r.net, "model_cover": max(lvl, float(b.open)), "bar_ts": b.ts})
    return pd.DataFrame(rows)


def fill_from(prints: list, level: float) -> tuple:
    """(trigger ts, trigger px, fill, n prints in window) from SIP trades sorted by time; None fields if no trigger."""
    kept = []
    for x in prints:
        if not isinstance(x, dict):
            continue
        p, s, c = x.get("p"), x.get("s"), x.get("c") or []
        if not isinstance(p, (int, float)) or not isinstance(s, int) or p <= 0 or s <= 0:
            continue
        if any(str(k) in DROP for k in (c if isinstance(c, list) else [])):
            continue
        kept.append((pd.Timestamp(x.get("t")), float(p), s))
    trig = next((k for k in kept if k[1] >= level), None)
    if trig is None:
        return None, None, None, 0
    win = [k for k in kept if trig[0] <= k[0] <= trig[0] + WINDOW]
    vwap = sum(p * s for _, p, s in win) / sum(s for _, _, s in win)
    return trig[0].isoformat(), trig[1], max(vwap, trig[1]), len(win)


def cmd_fetch() -> int:
    from fade_live import data_client

    OUT.mkdir(parents=True, exist_ok=True)
    done = {(r["symbol"], r["session"]) for r in csv.DictReader(CACHE.open())} if CACHE.exists() else set()
    t = stopped()
    print(f"stopped trades: {len(t)}", flush=True)
    new = not CACHE.exists()
    with CACHE.open("a", newline="") as f, data_client() as c:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, r in enumerate(t.itertuples()):
            if (r.symbol, r.session) in done:
                continue
            prints, token, ok = [], None, True
            for _ in range(MAX_PAGES):
                params = {"start": r.bar_ts.isoformat(), "end": (r.bar_ts + pd.Timedelta(seconds=60)).isoformat(),
                          "feed": "sip", "limit": 10000}
                if token:
                    params["page_token"] = token
                j = _get(c, f"/v2/stocks/{r.symbol}/trades", params)
                if j is None:
                    ok = False
                    break
                prints += j.get("trades") or []
                token = j.get("next_page_token")
                if not token or any(isinstance(x, dict) and isinstance(x.get("p"), (int, float)) and x["p"] >= r.level
                                    for x in prints):
                    break
            if not ok:
                continue
            ts, px, fill, n = fill_from(prints, r.level)
            w.writerow({"symbol": r.symbol, "session": r.session, "split": r.split, "entry": r.entry, "level": r.level,
                        "net": r.net, "model_cover": r.model_cover, "trigger_ts": ts, "trigger_px": px, "fill": fill,
                        "n": n})
            if i % 100 == 0:
                f.flush()
                print(f"{i}/{len(t)}", flush=True)
    return 0


def cmd_report() -> int:
    q = pd.read_csv(CACHE)
    q["fill_used"] = q.fill.where(q.fill.notna(), q.model_cover)
    q["slip"] = q.fill / q.level - 1
    q["adj"] = (q.fill_used - q.model_cover) / q.entry
    out = {}
    for split in ("dev", "holdout"):
        ev = pd.read_csv(f"reports/fade_news/{split}_events.csv")[["symbol", "session", "net"]]
        ev["session"] = ev.session.astype(str)
        s = q[q.split == split].assign(session=lambda d: d.session.astype(str))
        m = ev.merge(s[["symbol", "session", "adj"]], on=["symbol", "session"], how="left").fillna({"adj": 0.0})
        r = {"stops": len(s), "found": int(s.fill.notna().sum()), "slip_mean": float(s.slip.mean()),
             "slip_median": float(s.slip.median()), "slip_p90": float(s.slip.quantile(0.9)),
             "net": float(m.net.mean()), "net_real_stops": float((m.net - m.adj).mean())}
        band = pd.cut(s.entry, [1, 2, 5, 10, 50.01], labels=["$1-2", "$2-5", "$5-10", "$10-50"], right=False)
        r["slip_by_band"] = {str(k): float(v) for k, v in s.groupby(band, observed=True).slip.mean().items()}
        out[split] = r
        print(f"{split:7s} stops {r['stops']} (trigger found {r['found']}): slippage mean {r['slip_mean']:+.2%} median "
              f"{r['slip_median']:+.2%} p90 {r['slip_p90']:+.2%} | net {r['net']:+.2%} -> {r['net_real_stops']:+.2%} "
              f"| by band {({k: round(v * 100, 2) for k, v in r['slip_by_band'].items()})}")
    (OUT / "report.json").write_text(json.dumps(out, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "report"])
    return {"fetch": cmd_fetch, "report": cmd_report}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
