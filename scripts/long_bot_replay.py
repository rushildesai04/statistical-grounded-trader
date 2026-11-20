"""The long book replayed as the BOT, not as a study (owner 2026-11-19: "actually simulate with historical data with
our bot so we can compare results"; docs/findings/long_bot_replay_2026-11-19.md).

For every alert a fresh ops database gets the alert row. Then the real code runs on it as it runs live:
``long_book.tick`` once a minute on the free feed's candles that had CLOSED by then (entry, the no-win skip, size, the
heads-up text, exits), then the real nightly path on the official tape, ``long_book.settle`` and
``text_order.run`` (a texted long the tested order cannot fill is booked as bought at the text). Configurations on the
same alerts:
    old    the one-minute setups the book bought until 2026-11-19
    new    the live book as it is now (base break + flag)
    new8   the base break with the 8% width it shipped with for a few hours on 2026-11-19

    python scripts/long_bot_replay.py history [--workers 8] [--configs old,new]
    python scripts/long_bot_replay.py alerts --alerts rows.json --bars DIR      # rows: symbol, session, posted_at[, halt]
    python scripts/long_bot_replay.py read [--name history]                      # DIR: iex_SYM_DATE.parquet, sip_SYM_DATE.parquet

A measurement tool: it writes a CSV under reports/long_bot_replay and changes nothing. On 2026-11-19 it reproduced
the live book's 21 settled trades with the same setup, 17 of them to the exact official result."""
from __future__ import annotations

import argparse
import itertools
import json
import shutil
import sys
import tempfile
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from multiprocessing import Pool
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.decision.features import CostModelAdapter
from sgt.fast import fade
from sgt.fast import long_book as lb
from sgt.fast import long_shadow as ls
from sgt.fast import patterns as pt
from sgt.fast import text_order, trader_rule
from sgt.ops.store import OpsStore

OUT = Path("reports/long_bot_replay")
ET = ZoneInfo("America/New_York")
LEGACY = ("L5v2", "L5", "L4b", "L6", "L4a", "L7b", "L7a")
# name -> (the kinds the live book takes, how long an alert is watched, the base's width cap; None = as the code has it)
CONFIGS = {"old": (LEGACY, pt.WATCH, None), "new": (("L8", "L5v2"), max(pt.WATCH, pt.BASE_WATCH), None),
           "new8": (("L8", "L5v2"), max(pt.WATCH, pt.BASE_WATCH), 0.08)}
MAX_ALERTS = 20_000
COLS = ["ts", "open", "high", "low", "close", "volume"]
_CM = None


def cost_fn(sym, ts, px) -> float:
    """The round-trip cost the nightly settle uses (``fade_live.sip_session_tools``)."""
    global _CM
    if _CM is None:
        _CM = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT, rest_mult=GAPPER_REST_MULT)
    return float(_CM.round_trip(sym, ts, px)[0])


@contextmanager
def configured(cfg: str):
    """The live book set to one configuration; everything is put back afterwards."""
    kinds, watch, width = CONFIGS[cfg]
    keep = (lb.LIVE_KINDS, lb.LIVE_WATCH, pt.BASE_MAX_RANGE, trader_rule.track, lb.matrix_line)
    lb.LIVE_KINDS, lb.LIVE_WATCH = kinds, watch
    pt.BASE_MAX_RANGE = keep[2] if width is None else width
    trader_rule.track = lambda *a, **k: None            # a measurement beside the book: not part of what it trades
    lb.matrix_line = lambda *a, **k: ""                 # an extra line of a text: not part of what it trades
    lb._looked.clear()
    try:
        yield
    finally:
        lb.LIVE_KINDS, lb.LIVE_WATCH, pt.BASE_MAX_RANGE, trader_rule.track, lb.matrix_line = keep
        lb._looked.clear()


def frame(f) -> pd.DataFrame:
    """Regular-session candles, time-ordered, with ``ts`` as UTC timestamps; an empty frame when there are none."""
    if f is None or not len(f):
        return pd.DataFrame(columns=COLS)
    f = f[f.segment == "rth"] if "segment" in f else f
    return f.assign(ts=pd.to_datetime(f.ts, utc=True)).sort_values("ts").reset_index(drop=True)


def one(job, tmp: Path | None = None, cost=cost_fn) -> dict:
    """One alert under one configuration: (cfg, alert, feed candles, official candles) -> the long book's own row."""
    cfg, ev, feed, tape = job
    base = {"cfg": cfg, "symbol": ev["symbol"], "session": ev["session"], "split": ev.get("split")}
    tmp = tmp or Path(tempfile.gettempdir())
    p = tmp / f"botreplay-{uuid.uuid4().hex}.db"
    try:
        with configured(cfg):
            db = OpsStore(p)
            posted = pd.Timestamp(ev["posted_at"])
            posted = posted.tz_localize("UTC") if posted.tzinfo is None else posted.tz_convert("UTC")
            status, reason = ("skipped", ls.HALT_REASON) if ev.get("halt") else ("pending", None)
            with db.tx() as c:
                c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                          "weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (ev["symbol"], ev["session"], "volume_spike", posted.isoformat(), "-1", 1, status, reason, 0.05,
                           posted.isoformat()))
            d = date.fromisoformat(ev["session"])
            close = pd.Timestamp(datetime.combine(d, time(16, 0), ET)).tz_convert("UTC")
            closes = set((feed.ts + pt.BAR).dt.floor("min")) if len(feed) else set()
            texts, arms, row = [], [], None
            now = posted.floor("min") + pt.BAR + pd.Timedelta(seconds=5)
            end = min(close + pd.Timedelta(minutes=3), posted + pd.Timedelta(minutes=140))
            k = 0
            while now <= end:                           # a pass when a candle has just closed, while a long is open, and
                if (row is not None and row["status"] == "open") or now.floor("min") in closes or k % 5 == 0:   # every 5 min
                    lb.tick(db, lambda s_, n_: feed, texts.append, now=now.to_pydatetime(), arm=arms.append)
                    with db.tx() as c:
                        r = c.execute("SELECT * FROM long_trades").fetchone()
                    row = dict(r) if r else None
                    if row is not None and row["status"] != "open":
                        break
                now += pt.BAR
                k += 1
            night = datetime.combine(d, time(20, 40), ET)
            later = night + timedelta(days=lb.STALE_DAYS + 2)
            bars = lambda s_, ses: tape  # noqa: E731
            lb.settle(db, bars, cost, now=night)
            text_order.run(db, lambda *a: None, bars, cost, now=later)      # no stored prints: the next official candle's open
            lb.settle(db, bars, cost, now=later)
            with db.tx() as c:
                r = c.execute("SELECT * FROM long_trades").fetchone()
        row = dict(r) if r else {}
        risk = (row["est_entry"] - row["stop"]) / row["est_entry"] if row.get("est_entry") and row.get("stop") else None
        return {**base, "status": row.get("status", "norow"), "kind": row.get("kind"), "rules": row.get("rules"),
                "reason": row.get("reason"), "est_entry": row.get("est_entry"), "est_exit": row.get("est_exit"),
                "entry": row.get("entry"), "exit": row.get("exit"), "exit_reason": row.get("exit_reason"),
                "gross": row.get("gross"), "cost": row.get("cost_rt"), "net": row.get("net"), "weight": row.get("weight"),
                "risk": risk, "entry_ts": row.get("entry_ts"), "texts": len(texts), "armed": len(arms)}
    except Exception as exc:  # noqa: BLE001 - one alert failing is a row that says so, never a lost run
        return {**base, "status": f"ERROR {type(exc).__name__}: {exc}"[:160]}
    finally:
        for q in (p, Path(str(p) + "-wal"), Path(str(p) + "-shm")):
            q.unlink(missing_ok=True)


def jobs_history(configs: list[str]):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import long_feed_replay_study as fr

    from sgt.intraday.store import ParquetBarStore

    store = ParquetBarStore("data/alpaca")
    for split in ("train", "holdout"):
        for e in fr.events(split).itertuples(index=False):
            feed = fr.feed_bars(e.symbol, e.session)
            d = date.fromisoformat(e.session)
            tape = frame(store.read_frame(e.symbol, d, d))
            if feed is None or not len(tape):
                continue
            ev = {"symbol": e.symbol, "session": e.session, "posted_at": str(e.posted_at), "halt": bool(e.halt), "split": split}
            for cfg in configs:
                yield cfg, ev, frame(feed), tape


def jobs_alerts(rows: list, bars: Path, configs: list[str]):
    for r in rows[:MAX_ALERTS]:
        if not isinstance(r, dict) or not all(isinstance(r.get(k), str) for k in ("symbol", "session", "posted_at")):
            continue
        a, b = (bars / f"{feed}_{r['symbol']}_{r['session']}.parquet" for feed in ("iex", "sip"))
        if not (a.is_file() and b.is_file()):
            continue
        halt = bool(r.get("halt")) or (r.get("status") in ("skipped", "void") and r.get("reason") == ls.HALT_REASON)
        ev = {"symbol": r["symbol"], "session": r["session"], "posted_at": r["posted_at"], "split": "alerts", "halt": halt}
        for cfg in configs:
            yield cfg, ev, frame(pd.read_parquet(a)), frame(pd.read_parquet(b))


def summary(t: pd.DataFrame) -> list[str]:
    """Per sample and configuration: trades, average and median official result, wins, the result per dollar at the
    book's risk size, dollars at the size booked, months above zero; then the same alerts both configurations trade."""
    out = []
    for sp in [s for s in t.split.dropna().unique()]:
        q = t[t.split == sp]
        out.append(f"=== {sp}: {q[['symbol', 'session']].drop_duplicates().shape[0]} alerts")
        nets = {}
        for cfg in [c for c in CONFIGS if (q.cfg == c).any()]:
            g = q[(q.cfg == cfg) & (q.status == "settled")].dropna(subset=["net"])
            nets[cfg] = g.set_index(["symbol", "session"]).net
            if not len(g):
                out.append(f"  {cfg:5s} no trades")
                continue
            w, x = g.weight.fillna(0).to_numpy(float), g.net.to_numpy(float)
            bym = g.assign(m=g.session.str[:7]).groupby("m").net.mean()
            per = (w * x).sum() / w.sum() if w.sum() else float("nan")
            out.append(f"  {cfg:5s} trades {len(g):5d} | avg {x.mean():+.2%} | median {np.median(x):+.2%} | wins {(x > 0).mean():.0%} | "
                       f"per dollar at risk size {per:+.2%} | dollars as booked {(w * x * fade.NAV0).sum():+,.0f} | "
                       f"months above zero {(bym > 0).sum()} of {len(bym)}")
        for a, b in itertools.combinations(nets, 2):
            both = nets[a].index.intersection(nets[b].index)
            if len(both):
                out.append(f"  same alerts, {a} and {b} both trade: {len(both)} | {a} {nets[a][both].mean():+.2%} vs {b} {nets[b][both].mean():+.2%}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("history", "alerts", "read"))
    ap.add_argument("--configs", default="old,new")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--alerts", type=Path)
    ap.add_argument("--bars", type=Path)
    ap.add_argument("--name")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    name = a.name or ("history" if a.cmd != "alerts" else "alerts")
    path = a.out / f"{name}.csv"
    if a.cmd == "read":
        print("\n".join(summary(pd.read_csv(path, dtype={"symbol": str}))))
        return 0
    configs = [c for c in a.configs.split(",") if c in CONFIGS]
    if not configs or not 1 <= a.workers <= 32:
        raise SystemExit("unknown configuration or worker count")
    if a.cmd == "alerts":
        if a.alerts is None or a.bars is None:
            raise SystemExit("alerts needs --alerts and --bars")
        src = jobs_alerts(json.loads(a.alerts.read_text()), a.bars, configs)
    else:
        src = jobs_history(configs)
    tmp = Path(tempfile.mkdtemp(prefix="botreplay"))
    try:
        with Pool(a.workers) as pool:
            rows = list(pool.imap_unordered(one, src, chunksize=4))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    t = pd.DataFrame(rows)
    a.out.mkdir(parents=True, exist_ok=True)
    t.to_csv(path, index=False)
    print("\n".join(summary(t)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
