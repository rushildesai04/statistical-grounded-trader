"""Real quoted spreads at the fade's entry and exit vs the cost model (2026-11-08).

    python scripts/fade_spread_audit.py fetch     # resumable: SIP quotes at entry and entry+60 min per trade
    python scripts/fade_spread_audit.py report    # measured vs modelled round trip, net re-scored

The champion's net subtracts a modelled round trip (``cost_rt``: twice a calibrated half-spread by price bucket,
x1.38 in the first hour). A market short sells at the bid and covers at the ask, so the real cost of crossing is the
quoted half-spread at each fill. This measures it from the first SIP quotes at the entry minute (the tested fill is
that bar's open) and at entry + 60 minutes (the timed exit; stopped trades exit earlier, so their exit spread is an
approximation). A measurement of an assumption, not a rule search: nothing is selected on these numbers.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("reports/fade_spreads")
CACHE = OUT / "quotes.csv"
FIELDS = ["symbol", "session", "leg", "ts", "n", "hs_med", "hs_mean", "mid"]
N_QUOTES = 50


def half_spreads(quotes: list) -> tuple[int, float | None, float | None, float | None]:
    """Median / mean quoted half-spread (fraction of mid) over valid quotes (bid > 0, ask > bid, sane width)."""
    hs, mids = [], []
    for q in quotes if isinstance(quotes, list) else []:
        bp, ap = q.get("bp"), q.get("ap")
        if not isinstance(bp, (int, float)) or not isinstance(ap, (int, float)) or bp <= 0 or ap <= bp:
            continue
        mid = (ap + bp) / 2
        h = (ap - bp) / 2 / mid
        if h < 0.25:                                          # > 50% wide = a stub quote, not a market
            hs.append(h)
            mids.append(mid)
    if not hs:
        return 0, None, None, None
    return len(hs), float(np.median(hs)), float(np.mean(hs)), float(np.median(mids))


def trades() -> pd.DataFrame:
    rows = []
    for name in ("dev", "holdout"):
        e = pd.read_csv(f"reports/fade_news/{name}_events.csv")[["symbol", "session", "net"]].assign(split=name)
        rows.append(e)
    e = pd.concat(rows)
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "entry_ts", "entry", "cost_rt"]]
    return e.merge(src, on=["symbol", "session"], how="left").dropna(subset=["net", "entry_ts"])


def cmd_fetch() -> int:
    sys.path.insert(0, "scripts")
    from fade_live import data_client

    OUT.mkdir(parents=True, exist_ok=True)
    done = set()
    if CACHE.exists():
        done = {(r["symbol"], r["session"], r["leg"]) for r in csv.DictReader(CACHE.open())}
    t = trades()
    new = not CACHE.exists()
    with CACHE.open("a", newline="") as f, data_client() as c:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, r in enumerate(t.itertuples()):
            for leg, at in (("entry", pd.Timestamp(r.entry_ts)), ("exit", pd.Timestamp(r.entry_ts) + pd.Timedelta(minutes=60))):
                if (r.symbol, str(r.session), leg) in done:
                    continue
                for attempt in range(5):
                    try:
                        resp = c.get(f"/v2/stocks/{r.symbol}/quotes", params={
                            "start": at.isoformat(), "end": (at + pd.Timedelta(seconds=60)).isoformat(),
                            "feed": "sip", "limit": N_QUOTES}, timeout=15)
                    except Exception as exc:  # noqa: BLE001 - network blip: back off and retry, then record unknown
                        print(f"{r.symbol} {leg}: {type(exc).__name__}", flush=True)
                        time.sleep(2 ** attempt)
                        continue
                    if resp.status_code == 429:
                        time.sleep(min(60, 5 * 2 ** attempt))
                        continue
                    break
                else:
                    continue                                   # gave up: not cached, a re-run retries it
                quotes = resp.json().get("quotes") if resp.status_code == 200 else None
                n, hm, ha, mid = half_spreads(quotes or [])
                w.writerow({"symbol": r.symbol, "session": r.session, "leg": leg, "ts": at.isoformat(), "n": n,
                            "hs_med": hm, "hs_mean": ha, "mid": mid})
                time.sleep(0.31)                               # ~190 requests/min, under the 200/min data limit
            if i % 200 == 0:
                f.flush()
                print(f"{i}/{len(t)}", flush=True)
    return 0


def cmd_report() -> int:
    t = trades()
    q = pd.read_csv(CACHE)
    q["session"] = q.session.astype(str)
    t["session"] = t.session.astype(str)
    p = q.pivot_table(index=["symbol", "session"], columns="leg", values="hs_med", aggfunc="first")
    p = p.rename(columns={"entry": "hs_entry", "exit": "hs_exit"}).reset_index()
    t = t.merge(p, on=["symbol", "session"], how="left").dropna(subset=["hs_entry", "hs_exit"])
    t["measured_rt"] = t.hs_entry + t.hs_exit
    t["net_measured"] = t.net + t.cost_rt - t.measured_rt
    t["band"] = pd.cut(t.entry, [1, 2, 5, 10, 50.01], labels=["$1-2", "$2-5", "$5-10", "$10-50"], right=False)
    out = {}
    for split, g in t.groupby("split"):
        rows = {}
        for band, b in [("all", g), *g.groupby("band", observed=True)]:
            rows[str(band)] = {"n": len(b), "model_rt": float(b.cost_rt.mean()), "measured_rt": float(b.measured_rt.mean()),
                               "measured_rt_median": float(b.measured_rt.median()), "hs_entry": float(b.hs_entry.mean()),
                               "hs_exit": float(b.hs_exit.mean()), "net_model": float(b.net.mean()),
                               "net_measured": float(b.net_measured.mean())}
        out[split] = rows
        print(f"\n{split} (n {len(g)} with quotes at both legs)")
        print(f"{'band':7s} {'n':>5s} {'model rt':>9s} {'real rt':>8s} {'real med':>9s} {'entry hs':>9s} {'exit hs':>8s} "
              f"{'net model':>10s} {'net real':>9s}")
        for band, r in rows.items():
            print(f"{band:7s} {r['n']:5d} {r['model_rt']:9.2%} {r['measured_rt']:8.2%} {r['measured_rt_median']:9.2%} "
                  f"{r['hs_entry']:9.2%} {r['hs_exit']:8.2%} {r['net_model']:+10.2%} {r['net_measured']:+9.2%}")
    cov = {s: float(len(g)) for s, g in t.groupby("split")}
    out["coverage"] = cov
    (OUT / "report.json").write_text(json.dumps(out, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "report"])
    return {"fetch": cmd_fetch, "report": cmd_report}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
