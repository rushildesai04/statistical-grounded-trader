"""Descriptive only (run after both studies): the frozen bull flag and BoS rules on the Nov 2026 forward alert events.
(their event file ends 2026-10-29). Never decides anything."""
import json
import sys
from datetime import date
sys.path.insert(0, "scripts")
import pandas as pd
import bull_flag_finder_study as bfs, bos_ema_study as bos
from alert_fade_forward import CHANNEL, DB, forward_events
from telegram_alert_test import load_alerts
from sgt.intraday.store import ParquetBarStore
ev = forward_events(load_alerts(DB, CHANNEL), ParquetBarStore("data/alpaca"), date(2026, 11, 12), fill_bars=False)
ev = ev[ev.status == "ok"].assign(t0=lambda d: pd.to_datetime(d.entry_ts, utc=True), halt=False)[["symbol", "session", "t0", "cost_rt", "halt"]]
print("events", len(ev), ev.session.min(), ev.session.max())
full = bfs.FullBars()
a = pd.DataFrame(bfs.event_rows(ev, full)); b = pd.DataFrame(bos.event_rows(ev, full)[0])
out = {}
for name, t, cands in (("BF", a, bfs.CANDIDATES), ("BOS", b, bos.CANDIDATES)):
    for c in cands:
        x = t[t.candidate == c]
        out[c] = {k: v for k, v in bfs.stats(x).items() if k in ("n", "mean", "median", "win", "p", "lower95", "exits")}
        if len(x): out[c]["sessions"] = int(x.session.nunique()); out[c]["top3_share_of_sum"] = float(x.net.nlargest(3).sum() / x.net.sum()) if x.net.sum() else None
print(json.dumps(out, indent=1, default=str))
xl = a[(a.symbol == "XLAB") & (a.candidate.isin(["B1X1", "B1X2"]))][["candidate", "entry_ts", "entry", "exit", "reason", "net", "strength"]]
print(xl.to_string()); print(b[(b.symbol == "XLAB") & (b.candidate.isin(["S1X1", "S1X2"]))][["candidate", "entry_ts", "entry", "exit", "reason", "net"]].to_string())
