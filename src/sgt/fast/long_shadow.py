"""Long shadow book: the tested long setups run on each day's real alerts, paper only (owner 2026-11-10: "paper-run the
longs"). No orders, no texts. Every setup of `prereg_long_setups_2026-11-10` (all FAILED on history) is run nightly,
after the fade settles, on the official SIP bars with the SAME code as the study (`sgt.fast.patterns`), so the live
record shows whether the history holds.

Population = the fade's research events: a settled fade row is a qualifying event (start bar t0 = the first SIP bar at or
after alert + 10 s, within 5 minutes; the tested timing, not an approval delay). A row voided with "no_entry" (no bar within 5 minutes) is
a halt event: L7 starts at the reopen bar (the first bar after the gap, within 30 minutes). Costs: the fade's stored
round trip; halt events (no stored cost) use ``cost_fn``. S2 (short) also pays the 0.5% locate. One row per
(fade row, setup kind); kind "-" marks a processed event with no setup, so a rerun never duplicates.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd

from sgt.fast import fade
from sgt.fast import patterns as pt
from sgt.ops.store import OpsStore, iso, utcnow

LOCATE = 0.005
REOPEN_MAX = pd.Timedelta(minutes=30)
LOOKBACK_DAYS = 14                  # review F-2: only recent events (bounds the first run and retries of missing bars)
HALT_REASON = "no_entry"          # attach_trades status: no bar within 5 min of the alert (a halt); fade_live._score
                                  # stores it as the void reason ("no bars in window" is a different case)
TESTED = {"L4a": -0.0119, "L4b": -0.0244, "L5": -0.0172, "L6": -0.0185, "L7a": -0.0187, "L7b": -0.0283,
          "S2": -0.0138}                  # training mean net per trade, prereg_long_setups_2026-11-10 (all FAIL)
Bars = Callable[[str, str], pd.DataFrame]   # (symbol, session) -> RTH 1-minute bars, ts = bar start UTC
Cost = Callable[[str, datetime, float], float]


def _latest(session: str) -> pd.Timestamp:
    d = date.fromisoformat(session)
    return pd.Timestamp(datetime.combine(d, fade.last_entry(d), tzinfo=fade.ET)).tz_convert("UTC") + pt.BAR


# Owner 2026-11-10 ("longs first"): longs are considered on every research alert, including the ones the SHORT
# skipped for company news (F6) or high beta (F7); those never settle as fades, so they are taken from 'filtered'.
SHORT_SKIPS = (fade.NEWS_REASON, fade.BETA_REASON)


def due(db: OpsStore, since: str) -> list[dict]:
    """Research events from ``since`` (ISO session date) on that have no shadow rows yet."""
    with db.tx() as c:
        rows = c.execute(
            "SELECT id, symbol, session, posted_at, status, reason, cost_rt FROM fade_trades f WHERE session>=? AND "
            "(status='settled' OR (status='void' AND reason=?) OR (status='filtered' AND reason IN (?,?))) AND NOT "
            "EXISTS (SELECT 1 FROM long_shadow s WHERE s.fade_id=f.id) ORDER BY id",
            (since, HALT_REASON, *SHORT_SKIPS)).fetchall()
    return [dict(r) for r in rows]


def setups(f: pd.DataFrame, row: dict) -> tuple[list[pt.Setup], pd.Timestamp | None]:
    """Setups for one event and its start bar (None = no start bar: nothing to run). Start bar exactly as the research
    (telegram_alert_test.attach_trades): the first bar at or after alert + 10 s, starting within 5 minutes of the alert;
    for a halt event the first bar after the gap, within 30 minutes of the alert."""
    posted = pd.Timestamp(row["posted_at"]).tz_convert("UTC")
    nxt = f[f.ts >= posted + pd.Timedelta(fade.REACTION)]
    if nxt.empty:
        return [], None
    t0 = nxt.ts.iloc[0]
    if row["status"] in ("settled", "filtered"):
        if t0 - posted > pd.Timedelta(fade.MAX_ENTRY_DELAY):
            return [], None
        if row["status"] == "filtered" and not fade.PRICE_MIN <= float(nxt.open.iloc[0]) <= fade.PRICE_MAX:
            return [], None                       # the research $1-50 filter at the start bar (settled rows passed it)
        return pt.first_red(f, t0) + pt.bull_flag(f, t0) + pt.bos_long(f, t0), t0
    if t0 - posted > REOPEN_MAX:
        return [], None
    return pt.first_red(f, t0, "L7"), t0


def run(db: OpsStore, bars: Bars, cost_fn: Cost, now: datetime | None = None) -> list[str]:
    """Shadow every due event once. Returns one line per filled shadow trade."""
    now, out = now or utcnow(), []
    since = (now - timedelta(days=LOOKBACK_DAYS)).date().isoformat()
    for row in due(db, since):
        f = bars(row["symbol"], row["session"])
        if not len(f):                  # review F-1: bars not fetched (outage): retry next run, never a permanent "-"
            continue
        found, t0 = setups(f, row)
        recs = []
        for s in found:
            t = pt.simulate(f, s, latest=_latest(row["session"]))
            rec = {"kind": s.kind, "side": s.side, "ready": iso(s.ready.to_pydatetime()), "trigger": s.trigger,
                   "stop": s.stop}
            if t is not None:
                rt = row["cost_rt"]
                if rt is None:
                    rt = cost_fn(row["symbol"], t.entry_ts.to_pydatetime(), t.entry)
                fee = rt + (LOCATE if s.side == "short" else 0.0)
                rec.update(entry_ts=iso(t.entry_ts.to_pydatetime()), entry=t.entry,
                           exit_ts=iso(t.exit_ts.to_pydatetime()), exit=t.exit, reason=t.reason, gross=t.gross,
                           net=t.gross - fee, cost_rt=rt)
                out.append(f"{row['symbol']} {s.kind} {t.gross - fee:+.2%}")
            recs.append(rec)
        if not recs:
            recs = [{"kind": "-", "side": None, "reason": "no setup" if t0 is not None else "no start bar"}]
        with db.tx() as c:
            for r in recs:
                c.execute("INSERT OR IGNORE INTO long_shadow(fade_id, symbol, session, kind, side, ready, trigger, "
                          "stop, entry_ts, entry, exit_ts, exit, reason, gross, net, cost_rt, created_at) VALUES "
                          "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (row["id"], row["symbol"], row["session"], r["kind"], r.get("side"), r.get("ready"),
                           r.get("trigger"), r.get("stop"), r.get("entry_ts"), r.get("entry"), r.get("exit_ts"),
                           r.get("exit"), r.get("reason"), r.get("gross"), r.get("net"), r.get("cost_rt"), iso(now)))
    return out


def record_line(db: OpsStore) -> str | None:
    """Digest RECORD line: live shadow longs vs what the test said (paper, no orders)."""
    with db.tx() as c:
        rows = c.execute("SELECT kind, COUNT(*) n, AVG(net) m FROM long_shadow WHERE net IS NOT NULL "
                         "GROUP BY kind ORDER BY kind").fetchall()
    if not rows:
        return None
    n = sum(r["n"] for r in rows)
    avg = sum(r["n"] * r["m"] for r in rows) / n
    parts = ", ".join(f"{r['kind']} {r['n']} avg {r['m']:+.1%}" for r in rows)
    return (f"long shadow (paper, no orders) {n} trades, avg {avg:+.2%}/trade: {parts}; "
            f"tested {min(TESTED.values()):+.1%} to {max(TESTED.values()):+.1%}")
