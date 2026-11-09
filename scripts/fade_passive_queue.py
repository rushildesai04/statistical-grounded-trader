"""Passive timed exit with a queue model on SIP trades (prereg_fade_passive_exit_2026-11-08, amendment A1).

    python scripts/fade_passive_queue.py fetch     # resumable: NBBO at t, NBBO at t+2m, kept prints in [t, t+2m)
    python scripts/fade_passive_queue.py report    # gate + secondary

Only timed exits (no stop inside the hour, exit + 2 min before 16:00 ET) are simulated; the rest are unchanged.
Quote sizes are taken as round lots of 100 shares [VERIFY against the Alpaca quote schema]; prints are filtered to
regular-way trades.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fade_passive_exit_study as pe  # noqa: E402
from alert_fade_test import HOLD_MIN  # noqa: E402
from fade_spread_audit import trades  # noqa: E402

OUT = Path("reports/fade_passive_exit")
CACHE = OUT / "queue.csv"
FIELDS = ["symbol", "session", "split", "entry", "net", "bid", "bid_lots", "ask", "ask_end", "n_kept", "vol_le_bid",
          "min_px", "pages"]
DROP = {"I", "B", "W", "Z", "4", "T", "U", "P", "V", "7"}
WAIT, MAX_PAGES, NOTIONAL = pd.Timedelta(minutes=pe.TIMEOUT), 5, 5000.0


def kept(trades_: list) -> list[tuple[float, int]]:
    out = []
    for x in trades_ if isinstance(trades_, list) else []:
        p, s, c = x.get("p"), x.get("s"), x.get("c") or []
        if not isinstance(p, (int, float)) or not isinstance(s, int) or p <= 0 or s <= 0:
            continue
        if any(str(k) in DROP for k in (c if isinstance(c, list) else [])) or x.get("x") == "D":
            continue                                  # odd lots etc., and off-exchange (TRF) prints
        out.append((float(p), s))
    return out


def timed_trades() -> pd.DataFrame:
    from sgt.intraday.store import ParquetBarStore

    store, rows = ParquetBarStore("data/alpaca"), []
    for r in trades().itertuples():
        d = date.fromisoformat(str(r.session))
        f = store.read_frame(r.symbol, d, d)
        if f.empty:
            continue
        f = f.assign(ts=pd.to_datetime(f.ts, utc=True))
        f = f[f.segment == "rth"]
        if pe.simulate(f, float(r.entry), pd.Timestamp(r.entry_ts), 1.0, 0.0)["kind"] == "timed":
            rows.append(r)
    return pd.DataFrame(rows)


def _get(c, path: str, params: dict) -> dict | None:
    for attempt in range(5):
        try:
            r = c.get(path, params=params, timeout=15)
        except Exception as exc:  # noqa: BLE001 - network blip: back off, retry, then give up (not cached)
            print(f"{path}: {type(exc).__name__}", flush=True)
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 429:
            time.sleep(min(60, 5 * 2 ** attempt))
            continue
        time.sleep(0.31)
        return r.json() if r.status_code == 200 else {}
    return None


def last_quote(c, symbol: str, at: pd.Timestamp) -> dict | None:
    j = _get(c, f"/v2/stocks/{symbol}/quotes", {"start": (at - pd.Timedelta(minutes=5)).isoformat(), "end": at.isoformat(),
                                                "feed": "sip", "limit": 1, "sort": "desc"})
    if j is None:
        return None
    q = (j.get("quotes") or [None])[0]
    return q if isinstance(q, dict) else {}


def cmd_fetch() -> int:
    from fade_live import data_client

    OUT.mkdir(parents=True, exist_ok=True)
    done = {(r["symbol"], r["session"]) for r in csv.DictReader(CACHE.open())} if CACHE.exists() else set()
    t = timed_trades()
    print(f"timed exits: {len(t)}", flush=True)
    new = not CACHE.exists()
    with CACHE.open("a", newline="") as f, data_client() as c:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for i, r in enumerate(t.itertuples()):
            if (r.symbol, str(r.session)) in done:
                continue
            at = pd.Timestamp(r.entry_ts) + pd.Timedelta(minutes=HOLD_MIN)
            q0, q1 = last_quote(c, r.symbol, at), last_quote(c, r.symbol, at + WAIT)
            if q0 is None or q1 is None:
                continue
            prints, token, pages = [], None, 0
            while pages < MAX_PAGES:
                params = {"start": at.isoformat(), "end": (at + WAIT).isoformat(), "feed": "sip", "limit": 10000}
                if token:
                    params["page_token"] = token
                j = _get(c, f"/v2/stocks/{r.symbol}/trades", params)
                if j is None:
                    break
                prints += kept(j.get("trades") or [])
                pages += 1
                token = j.get("next_page_token")
                if not token:
                    break
            if j is None:
                continue
            bid, ask = q0.get("bp"), q0.get("ap")
            ok = isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and 0 < bid < ask
            w.writerow({"symbol": r.symbol, "session": r.session, "split": r.split, "entry": r.entry, "net": r.net,
                        "bid": bid if ok else None, "bid_lots": q0.get("bs") if ok else None, "ask": ask if ok else None,
                        "ask_end": q1.get("ap"), "n_kept": len(prints),
                        "vol_le_bid": sum(s for p, s in prints if ok and p <= bid),
                        "min_px": min((p for p, _ in prints), default=None), "pages": pages})
            if i % 100 == 0:
                f.flush()
                print(f"{i}/{len(t)}", flush=True)
    return 0


def cmd_report() -> int:
    q = pd.read_csv(CACHE)
    allt = trades()
    out = {}
    for split in ("dev", "holdout"):
        d = q[(q.split == split) & q.bid.notna() & q.ask.notna()].copy()
        need = d.bid_lots.fillna(0) * 100 + NOTIONAL / d.entry
        d["filled"] = d.vol_le_bid >= need
        end_ask = d.ask_end.where(d.ask_end > 0, d.ask)
        d["delta"] = np.where(d.filled, d.ask - d.bid, d.ask - end_ask) / d.entry
        base = allt[allt.split == split][["symbol", "session"]].astype(str)
        full = base.merge(d.assign(session=d.session.astype(str))[["symbol", "session", "delta", "filled"]],
                          on=["symbol", "session"], how="left")
        full["delta"] = full.delta.fillna(0.0)                    # stopped / near close / no quote: unchanged
        full = full.merge(allt[["symbol", "session", "net", "entry"]].astype({"session": str}), on=["symbol", "session"])
        g = pe.gate(full.assign(kind=np.where(full.filled.notna(), "timed", "other"),
                                filled=full.filled.astype(object)))
        g["queue_fill_rate"] = float(d.filled.mean())
        out[split] = g
        print(f"{split:7s} simulated {len(d)}: queue fill {d.filled.mean():.0%} delta {g['mean_delta']:+.3%}/trade "
              f"(lower {g['lower']:+.3%}) filled {d.delta[d.filled].mean():+.2%} unfilled {d.delta[~d.filled].mean():+.2%} "
              f"net {g['net']:+.2%} -> {g['net_passive']:+.2%} -> {'PASS' if g['pass'] else 'fail'}"
              f"{' (decides)' if split == 'dev' else ''}")
    (OUT / "queue_report.json").write_text(json.dumps(out, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "report"])
    return {"fetch": cmd_fetch, "report": cmd_report}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
