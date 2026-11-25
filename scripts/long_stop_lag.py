"""How long after the official tape does the live long book see a stop? (docs/findings/long_stop_detection_2026-11-25.md)

READ-ONLY measurement: the ops database is opened read-only; per trade one official trade list and one free-feed trade
list are requested (throttled). For each long whose LIVE exit was a stop it prints, in seconds after the tape's first
print at or under the stop: the free feed's first such print (any size, and round lots), the close of the candle the
book exited on, and the book's own decision when the decision record has it. Counts and timings only; no return is
read. The trades it covers were already reviewed: development data, not a test.

Run on the runner (official data must be at least 15 minutes old):  python scripts/long_stop_lag.py
"""
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent)) if "__file__" in globals() else sys.path.insert(0, "/app/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src")) if "__file__" in globals() else sys.path.insert(0, "/app/src")
os.chdir(os.environ.get("SGT_HOME") or ".")
import httpx, numpy as np, pandas as pd
import exec_ledger as el
from sgt.fast import exec_model as em
from sgt.core import secrets
ET = "America/New_York"
def et(t): return pd.Timestamp(t).tz_convert(ET).strftime("%H:%M:%S")
c = sqlite3.connect("file:reports/ops/ops.db?mode=ro", uri=True, timeout=5); c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("SELECT id, symbol, session, kind, stop, entry_ts, created_at, est_entry, est_exit, est_exit_at, reason FROM long_trades WHERE est_exit_at IS NOT NULL AND stop IS NOT NULL AND (reason='stop' OR reason LIKE '%live exit stop%' OR reason LIKE 'official%') ORDER BY id")]
dec = {}
for r in c.execute("SELECT ref, at, reason FROM decisions WHERE book='long' AND state_to='est_closed' ORDER BY id"): dec.setdefault(r["ref"], (r["at"], r["reason"]))
live_stop = [r for r in rows if r["reason"] == "stop" or (dec.get(r["id"], (None, ""))[1] == "stop") or "live exit stop" in str(r["reason"])]
print("longs with a live stop exit:", len(live_stop), "of", c.execute("SELECT COUNT(*) FROM long_trades WHERE est_entry IS NOT NULL AND status IN ('est_closed','settled','void')").fetchone()[0], "entered")
h = {"APCA-API-KEY-ID": secrets.get_secret("alpaca_key_id") or "", "APCA-API-SECRET-KEY": secrets.get_secret("alpaca_secret_key") or ""}
out = []
with httpx.Client(base_url=el.BASE, headers=h, timeout=el.TIMEOUT) as client:
    api = el.Api(client)
    for r in live_stop[-22:]:
        try:
            a = pd.Timestamp(r["created_at"]); bar = pd.Timestamp(r["est_exit_at"]); b = bar + pd.Timedelta(minutes=3)
            if b <= a: continue
            tr = api.trades(r["symbol"], a, b)
            ts = pd.to_datetime(tr[:, 0].astype("int64"), utc=True); k = np.where(tr[:, 1] <= r["stop"])[0]
            tape = ts[k[0]] if len(k) else None
            time.sleep(0.4)
            q = client.get(f"/v2/stocks/{r['symbol']}/trades", params={"feed": "iex", "start": a.isoformat(), "end": b.isoformat(), "limit": 10000}); it = q.json().get("trades") or []
            fr = [x for x in it if float(x["p"]) <= r["stop"]]; frl = [x for x in fr if "I" not in (x.get("c") or [])]
            feed_any = pd.Timestamp(fr[0]["t"]) if fr else None; feed_rl = pd.Timestamp(frl[0]["t"]) if frl else None
            decided = pd.Timestamp(dec[r["id"]][0]) if r["id"] in dec else None
            close = bar + pd.Timedelta(minutes=1)
            f = lambda x: None if x is None or tape is None else round((x - tape).total_seconds())
            out.append({"id": r["id"], "sym": r["symbol"], "day": r["session"][5:], "tape": None if tape is None else et(tape), "feed_print_s": f(feed_any), "feed_roundlot_s": f(feed_rl), "candle_close_s": f(close), "decided_s": f(decided), "feed_prints_in_window": len(it)})
        except Exception as e:
            out.append({"id": r["id"], "sym": r["symbol"], "err": type(e).__name__})
    print("requests", api.n)
for o in out: print(json.dumps(o))
ok = [o for o in out if o.get("tape") and o.get("candle_close_s") is not None]
def med(k):
    v = sorted(o[k] for o in ok if o.get(k) is not None); return (len(v), v[len(v)//2] if v else None, v[-1] if v else None)
print("n with a tape crossing found:", len(ok))
for k in ("feed_print_s", "feed_roundlot_s", "candle_close_s", "decided_s"): print(k, "(n, median s, max s):", med(k))
print("no free-feed print at or under the stop in the window:", sum(o.get("feed_print_s") is None for o in ok), "| tape crossing not found:", sum(1 for o in out if not o.get("tape") and "err" not in o), "| errors:", sum("err" in o for o in out))
