"""Reconciled ledger: every paper trade re-priced by the execution model, next to its original result.

    python scripts/exec_ledger.py            # on the runner (it has the data keys); prints one JSON document

Model and labels: docs/findings/exec_model_2026-11-21.md (frozen before this was run). Read-only: the ops database is
opened read-only, nothing is written anywhere, the original ledger is never changed. Two populations:
  (a) the settled trades of the two alert books (shorts from the bot's decision, longs from their booking);
  (b) with --forward: every alert of the fade's forward record that has a stored receipt time, entered as the rule
      would with the alert known only at its receipt (owner 2026-11-21, decision 1 A).
External bytes (quotes, trades, bars): parsed strictly, pages bounded, every request throttled and timed out; a failed
request makes that one trade "no_data", never a number.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent) if "__file__" in globals() else "/app/scripts")

from sgt.decision.intrabar import clean_trades  # noqa: E402
from sgt.fast import exec_model as em  # noqa: E402
from sgt.fast import fade  # noqa: E402
from sgt.intraday.calendar import default_calendar  # noqa: E402

ET = ZoneInfo("America/New_York")
BASE = "https://data.alpaca.markets"
THROTTLE, TIMEOUT = 0.32, 15.0            # seconds between requests (the account allows 200 a minute); per request
TRADE_PAGES, PAGE = 10, 10_000            # at most 100,000 trades of one span are read
BOOK = 100_000.0                          # the paper book's starting value: order size = weight x this
OPS_DB = "reports/ops/ops.db"
APPEND_DAYS, APPEND_MAX = 14, 60          # the nightly mode: how far back it looks, and trades of one book per run


class Api:
    """The official tape over HTTP: bounded, throttled, strictly parsed. A failed request raises ``em.NoData``."""

    def __init__(self, client: httpx.Client, throttle: float = THROTTLE) -> None:
        self.c, self.throttle, self.n = client, throttle, 0
        self._bars: dict[tuple[str, str], pd.DataFrame] = {}

    def _get(self, path: str, params: dict) -> dict:
        time.sleep(self.throttle)
        self.n += 1
        try:
            r = self.c.get(path, params=params)
            j = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError) as exc:
            raise em.NoData(f"{path}: {type(exc).__name__}") from exc
        if not isinstance(j, dict):
            raise em.NoData(f"{path}: HTTP {r.status_code}")
        return j

    def quote(self, symbol: str, at: pd.Timestamp) -> dict | None:
        """The last best-bid / best-ask quote at or before ``at`` that session; None when there is none."""
        start = datetime.combine(at.tz_convert(ET).date(), datetime.min.time(), ET).replace(hour=4)
        j = self._get(f"/v2/stocks/{symbol}/quotes", {"feed": "sip", "start": start.isoformat(), "end": at.isoformat(),
                                                      "limit": 1, "sort": "desc"})
        rows = j.get("quotes")
        q = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
        if q is None:
            return None
        num = lambda k: float(q[k]) if isinstance(q.get(k), (int, float)) and not isinstance(q.get(k), bool) else 0.0  # noqa: E731
        return {"bid": num("bp"), "ask": num("ap"), "bid_size": num("bs"), "ask_size": num("as"), "t": str(q.get("t"))[:35]}

    def trades(self, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
        rows, token = [], None
        for _ in range(TRADE_PAGES):
            p = {"feed": "sip", "start": start.isoformat(), "end": end.isoformat(), "limit": PAGE}
            if token:
                p["page_token"] = token
            j = self._get(f"/v2/stocks/{symbol}/trades", p)
            got = j.get("trades")
            if got is not None and not isinstance(got, list):
                raise em.NoData("trades: bad payload")
            rows += [x for x in (got or []) if isinstance(x, dict)]
            token = j.get("next_page_token")
            if not isinstance(token, str) or not token:
                return clean_trades(rows)
        raise em.NoData("trades: more than the page bound in one span")

    def day(self, symbol: str, session: str) -> pd.DataFrame:
        """The session's official 1-minute bars of every segment (04:00 to 20:00 ET), with ``rth`` marked."""
        key = (symbol, session)
        if key not in self._bars:
            d = date.fromisoformat(session)
            b = default_calendar().bounds(d)
            lo = datetime.combine(d, datetime.min.time(), ET).replace(hour=4)
            j = self._get(f"/v2/stocks/{symbol}/bars", {"timeframe": "1Min", "feed": "sip", "limit": 10_000,
                                                        "start": lo.isoformat(), "end": (lo + timedelta(hours=16)).isoformat()})
            ok = [x for x in (j.get("bars") or []) if isinstance(x, dict) and isinstance(x.get("t"), str)
                  and all(isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) for k in "ohlc")]
            f = pd.DataFrame({"ts": pd.to_datetime([x["t"] for x in ok], utc=True, errors="coerce"),
                              **{n: [float(x[k]) for x in ok] for k, n in zip("ohlc", ("open", "high", "low", "close"))}})
            f = f.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)
            f["rth"] = (f.ts >= pd.Timestamp(b.rth_open)) & (f.ts < pd.Timestamp(b.rth_close))
            f.attrs["close"] = pd.Timestamp(b.rth_close).tz_convert("UTC")
            self._bars[key] = f
        return self._bars[key]

    def prior(self, symbol: str, session: str) -> tuple[float | None, float | None, float | None]:
        """(prior close, prior low, the close before that) from unadjusted official daily bars."""
        d = date.fromisoformat(session)
        j = self._get(f"/v2/stocks/{symbol}/bars", {"timeframe": "1Day", "feed": "sip", "adjustment": "raw", "limit": 20,
                                                    "start": (d - timedelta(days=14)).isoformat(),
                                                    "end": (d - timedelta(days=1)).isoformat()})
        ok = [x for x in (j.get("bars") or []) if isinstance(x, dict)
              and all(isinstance(x.get(k), (int, float)) for k in ("c", "l"))]
        if not ok:
            return None, None, None
        return float(ok[-1]["c"]), float(ok[-1]["l"]), (float(ok[-2]["c"]) if len(ok) > 1 else None)

    def tape(self, symbol: str, session: str) -> em.Tape:
        f = self.day(symbol, session)
        return em.Tape(self.quote, self.trades, f[f.rth].reset_index(drop=True), f.attrs["close"])


def _t(x) -> pd.Timestamp | None:
    t = pd.to_datetime(x, utc=True, errors="coerce")
    return None if pd.isna(t) else t


def _plain(o):
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if isinstance(o, (np.floating, float)):
        return float(o) if math.isfinite(o) else None
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, tuple):
        return list(o)
    return str(o)


def _label(model: dict, flags: list[str]) -> str:
    if model.get("status") == "no_trade":      # amendment 1: its own label, as the spec's section 3 words it
        return "NO TRADE"
    bad = {"ssr", "no_borrow", "no_data", "unresolved"} & set(flags)
    return "UNRESOLVED" if model.get("status") != "ok" or bad else "MODELED"


def _ssr(api: Api, symbol: str, session: str, at: pd.Timestamp, out: dict | None = None) -> bool | None:
    """The short-sale price test at ``at``; ``out`` receives the numbers it was read from (for the ledger's reader)."""
    try:
        day, (pc, pl, ppc) = api.day(symbol, session), api.prior(symbol, session)
    except em.NoData:
        return None
    day = day[day.rth]              # amendment 1 (2026-11-21): the trigger is read from regular-hours trades only
    if out is not None:
        before = day[day.ts + em.MINUTE <= at]
        out["ssr_basis"] = {"prior_close": pc, "low_before_entry": float(before.low.min()) if len(before) else None,
                            "prior_low": pl, "close_before_prior": ppc}
    return em.ssr(day, at, pc, pl, ppc)


def short_row(api: Api, r: dict) -> dict:
    sym, ses, decided = r["symbol"], r["session"], _t(r["est_entry_at"])
    shares = (r.get("weight") or fade.WEIGHT) * BOOK / r["est_entry"] if r.get("est_entry") else None
    out = {"book": "short", "id": r["id"], "symbol": sym, "session": ses, "alert_type": r["alert_type"],
           "posted_at": r["posted_at"], "received_at": r["created_at"], "decided_at": r["est_entry_at"],
           # 2026-11-22 (listener recovery): how the alert reached the book; "replay" = recovered after a restart.
           # A label only: the trade is priced and counted exactly as any other (the cohort's definition is unchanged).
           "via": r.get("via"), "recovered_at": r.get("recovered_at"),
           "orig": {"entry_ts": r["entry_ts"], "entry": r["entry"], "exit": r["cover"], "reason": r["reason"], "net": r["net"],
                    "est_entry": r["est_entry"]}}
    try:
        tape = api.tape(sym, ses)
        m = em.short(tape, sym, decided, shares=shares)
        s5 = em.short(tape, sym, decided, latency=em.STRESS_LATENCY, shares=shares)
    except em.NoData as exc:
        m, s5 = {"status": "no_data", "why": str(exc), "flags": ("no_data",)}, {}
    flags = list(m.get("flags") or ())
    got, bar = _t(r["created_at"]), _t(r["entry_ts"])
    if got is not None and bar is not None and got > bar:
        flags.append("late")
    avail = r.get("ib_avail")
    if avail is None:
        flags.append("borrow_unknown")
    elif avail <= 0 or (shares and avail < shares):
        flags.append("no_borrow")
    elif r.get("alp_shortable") == 0:
        flags.append("borrow_conflict")
    if decided is not None and _ssr(api, sym, ses, decided + em.LATENCY, out):
        flags.append("ssr")
    return {**out, "model": m, "stress_latency_net": s5.get("net"), "flags": flags, "label": _label(m, flags)}


def long_row(api: Api, r: dict) -> dict:
    sym, ses, decided = r["symbol"], r["session"], _t(r["created_at"])
    shares = (r.get("weight") or fade.WEIGHT) * BOOK / r["est_entry"] if r.get("est_entry") else None
    out = {"book": "long", "id": r["id"], "symbol": sym, "session": ses, "kind": r["kind"], "rules": r["rules"],
           "decided_at": r["created_at"], "stop": r["stop"], "via": r.get("via"), "recovered_at": r.get("recovered_at"),
           "orig": {"entry_ts": r["fill_ts"], "entry": r["entry"], "exit": r["exit"], "reason": r["exit_reason"],
                    "net": r["net"], "est_entry": r["est_entry"], "live_reason": r["reason"]}}
    try:
        tape = api.tape(sym, ses)
        kw = {"red_exit": r["rules"] == "A2", "shares": shares}
        m = em.long(tape, sym, decided, float(r["stop"]), **kw)
        s5 = em.long(tape, sym, decided, float(r["stop"]), latency=em.STRESS_LATENCY, **kw)
    except em.NoData as exc:
        m, s5 = {"status": "no_data", "why": str(exc), "flags": ("no_data",)}, {}
    flags = list(m.get("flags") or ())
    return {**out, "model": m, "stress_latency_net": s5.get("net"), "flags": flags, "label": _label(m, flags)}


def forward_rows(api: Api, c: sqlite3.Connection, limit: int | None) -> tuple[list[dict], dict]:
    """Population (b): the forward record's events with a stored receipt time, entered at the later of the receipt and
    the tested minute's start."""
    from alert_fade_forward import CHANNEL, DB, forward_events
    from alert_fade_test import score
    from megacap_forward import last_complete_session
    from sgt.intraday.store import ParquetBarStore
    from telegram_alert_test import load_alerts

    store = ParquetBarStore("data/alpaca")
    ev = forward_events(load_alerts(DB, CHANNEL), store, last_complete_session(datetime.now(ET)), fill_bars=False)
    ev = ev[ev.status == "ok"].reset_index(drop=True)
    sc = score(ev, store)
    ev = ev.assign(tested_net=sc.net.to_numpy(), tested_gross=sc.gross.to_numpy())
    got = pd.read_sql("SELECT symbol, posted_at, created_at, ib_avail, status AS taken FROM fade_trades", c)
    got["posted_at"] = pd.to_datetime(got.posted_at, utc=True, format="ISO8601")
    got = got.sort_values("created_at").drop_duplicates(["symbol", "posted_at"])
    ev = ev.merge(got, on=["symbol", "posted_at"], how="left")
    counts = {"events": int(len(ev)), "no_receipt": int(ev.created_at.isna().sum())}
    ev = ev[ev.created_at.notna()]
    if limit:
        ev = ev.head(limit)
    rows = []
    for r in ev.itertuples(index=False):
        posted, received = pd.Timestamp(r.posted_at), _t(r.created_at)
        decided = max(received, pd.Timestamp(fade.entry_start(posted.to_pydatetime())))
        out = {"book": "forward", "symbol": r.symbol, "session": str(r.session), "alert_type": r.alert_type,
               "posted_at": posted, "received_at": received, "decided_at": decided, "taken": r.taken,
               "orig": {"entry_ts": pd.Timestamp(r.entry_ts), "entry": float(r.entry), "net": float(r.tested_net)
                        if pd.notna(r.tested_net) else None}}
        if decided - posted > pd.Timedelta(fade.MAX_ENTRY_DELAY):
            rows.append({**out, "model": {"status": "no_entry", "why": "received more than 5 minutes after posting"},
                         "flags": [], "label": "NO ENTRY"})
            continue
        try:
            tape = api.tape(r.symbol, str(r.session))
            m = em.short(tape, r.symbol, decided)
            s5 = em.short(tape, r.symbol, decided, latency=em.STRESS_LATENCY)
        except em.NoData as exc:
            m, s5 = {"status": "no_data", "why": str(exc), "flags": ("no_data",)}, {}
        flags = list(m.get("flags") or ())
        if received > pd.Timestamp(r.entry_ts):
            flags.append("late")
        if _ssr(api, r.symbol, str(r.session), decided + em.LATENCY, out):
            flags.append("ssr")
        if pd.notna(r.ib_avail) and r.ib_avail <= 0:
            flags.append("no_borrow")
        label = _label(m, flags)
        if m.get("status") == "ok":
            at = pd.Timestamp(m["entry_at"]).tz_convert(ET)
            mins = at.hour * 60 + at.minute
            if not (1 <= m["entry"] <= 50 and 9 * 60 + 30 <= mins <= 15 * 60 + 44) or at - posted.tz_convert(ET) > pd.Timedelta(fade.MAX_ENTRY_DELAY):
                label = "NOT AN EVENT AT THE FILL"
        rows.append({**out, "model": m, "stress_latency_net": s5.get("net"), "flags": flags, "label": label})
    return rows, counts


def settled_check(path: Path, db: str = OPS_DB) -> dict:
    """``exec_book.reconcile`` of ``path`` against the ids of every settled trade in the ops database (read-only)."""
    from sgt.fast import exec_book

    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        want = {"short": {r[0] for r in c.execute("SELECT id FROM fade_trades WHERE status='settled'")},
                "long": {r[0] for r in c.execute("SELECT id FROM long_trades WHERE status='settled'")}}
    finally:
        c.close()
    return exec_book.reconcile(want, path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--forward", action="store_true", help="also re-price the forward record (population b)")
    ap.add_argument("--limit", type=int, default=None, help="first N rows of each population (a dry run of the plumbing)")
    ap.add_argument("--since", default=None, help="sessions from this date (default: all; with --append the last 14 days)")
    ap.add_argument("--check", default=None, metavar="FILE",
                    help="no pricing: reconcile FILE against the settled trades of the ops database and print the result")
    ap.add_argument("--append", default=None, metavar="FILE",
                    help="the nightly mode: price only trades not yet in FILE and append them as JSON lines")
    args = ap.parse_args()
    from sgt.fast import exec_book

    if args.check:
        res = settled_check(Path(args.check))
        print(json.dumps(res))
        return 0 if res["ok"] else 2
    have = set()
    if args.append:                               # a trade that had no data is tried again; a priced one never is
        have = {(r["book"], r["id"]) for r in exec_book.rows(Path(args.append))
                if (r.get("model") or {}).get("status") not in ("no_data", "error")}
        args.since = args.since or (datetime.now(ET).date() - timedelta(days=APPEND_DAYS)).isoformat()
    args.since = args.since or "2026-01-01"
    from sgt.core.secrets import get_secret

    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    c = sqlite3.connect(f"file:{OPS_DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    lim = f" LIMIT {int(args.limit)}" if args.limit else ""
    shorts = [dict(r) for r in c.execute("SELECT * FROM fade_trades WHERE status='settled' AND session>=? ORDER BY id" + lim,
                                         (args.since,))]
    longs = [dict(r) for r in c.execute("SELECT * FROM long_trades WHERE status='settled' AND session>=? ORDER BY id" + lim,
                                        (args.since,))]
    if {"via", "recovered_at"} <= {r[1] for r in c.execute("PRAGMA table_info(fade_trades)")}:   # the alert's label
        how = {r["id"]: (r["via"], r["recovered_at"]) for r in c.execute(
            "SELECT id, via, recovered_at FROM fade_trades WHERE session>=? AND via IS NOT NULL", (args.since,))}
        for r in longs:
            r["via"], r["recovered_at"] = how.get(r["fade_id"], (None, None))
    shorts = [r for r in shorts if ("short", r["id"]) not in have][:APPEND_MAX if args.append else None]
    longs = [r for r in longs if ("long", r["id"]) not in have][:APPEND_MAX if args.append else None]
    out = {"meta": {"model": "exec_model v1", "latency_s": em.LATENCY.total_seconds(),
                    "stress_latency_s": em.STRESS_LATENCY.total_seconds(), "generated_at": datetime.now(timezone.utc).isoformat()}}
    with httpx.Client(base_url=BASE, headers=h, timeout=TIMEOUT) as client:
        api = Api(client)
        for name, rows, fn in (("shorts", shorts, short_row), ("longs", longs, long_row)):
            done = []
            for r in rows:
                try:
                    done.append(fn(api, r))
                except Exception as exc:  # noqa: BLE001 - one trade failing never stops the ledger; the row says so
                    done.append({"book": name, "id": r.get("id"), "symbol": r.get("symbol"), "session": r.get("session"),
                                 "model": {"status": "error", "why": type(exc).__name__}, "flags": ["no_data"],
                                 "label": "UNRESOLVED"})
                print(f"{name} {len(done)}/{len(rows)} requests {api.n}", file=sys.stderr, flush=True)
            out[name] = done
        if args.forward:
            try:
                import contextlib

                with contextlib.redirect_stdout(sys.stderr):      # the research loaders print their own counts
                    out["forward"], out["forward_counts"] = forward_rows(api, c, args.limit)
            except Exception as exc:  # noqa: BLE001 - the trades above are still printed; the failure is named
                import traceback

                traceback.print_exc()
                out["forward_error"] = f"{type(exc).__name__}: {exc}"[:300]
        out["meta"]["requests"] = api.n
    if args.append:
        path = Path(args.append)
        path.parent.mkdir(parents=True, exist_ok=True)
        new = out["shorts"] + out["longs"]
        with path.open("a") as fh:
            for r in new:
                fh.write(json.dumps({**r, "written_at": out["meta"]["generated_at"]}, default=_plain) + "\n")
        rc = settled_check(path)
        print("exec ledger reconciled: " + ("every settled trade is held once" if rc["ok"] else
                                            f"MISMATCH missing {rc['short']['missing']} {rc['long']['missing']} "
                                            f"extra {rc['short']['extra']} {rc['long']['extra']}"))
        print(f"exec ledger: {len(new)} trades priced ({api.n} requests); " + (exec_book.line(path, datetime.now(ET).date().isoformat()) or "no trade of the forward sample yet"))
        return 0
    print(json.dumps(out, default=_plain))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
