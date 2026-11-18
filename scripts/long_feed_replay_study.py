"""What the free feed's candles cost the long book, and does a pad under the stop repair it?
(docs/findings/prereg_long_feed_replay_2026-11-18.md)

    .venv/bin/python scripts/long_feed_replay_study.py --fetch     # free-feed bars of the event days (cached, throttled)
    .venv/bin/python scripts/long_feed_replay_study.py             # training, run ONCE

Report only: nothing here changes a book. The bar API is external: rows are parsed strictly and bounded."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_family_study import Bars, halt_events  # noqa: E402
from long_setups_study import D_STAR, latest  # noqa: E402
from trader_rules_long_study import boot_mean  # noqa: E402

from sgt.fast import long_book, long_shadow  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402

OUT = Path("reports/long_feed_replay")
FEED = Path("data/iex_1m")
BASE = Path("reports/long_risk/base.csv")
PADS = (0.005, 0.01, 0.02)
D50 = long_book.RISK_D50
ALPHA, SEED, N_BOOT = 0.05, 20261118, 10_000
THROTTLE_S = 1.5                                # the runner trades on this data account during the day: stay far below it
MAX_ROWS = 1_000                                # one regular session is 390 minutes
ET = ZoneInfo("America/New_York")
COLS = ["ts", "open", "high", "low", "close", "volume"]


def events(split: str = "train") -> pd.DataFrame:
    """The long book's tested events of one split: fades (research start bar) and halts (reopen bar), with the alert's
    posting time. The ticker NA is a ticker (converters)."""
    conv = {"symbol": str}
    e = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv", converters=conv)[["symbol", "session"]]
                   for n in ("dev", "holdout")])
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv", converters=conv)[
        ["symbol", "session", "entry_ts", "cost_rt", "posted_at"]]
    f = e.merge(src, on=["symbol", "session"]).dropna(subset=["entry_ts"])
    f = f.assign(session=f.session.astype(str), t0=pd.to_datetime(f.entry_ts, utc=True), halt=False)
    h_src = pd.read_csv("reports/fade_halt_audit/halt_events.csv", converters=conv)
    h = halt_events(Bars())
    h = h.merge(h_src.assign(session=h_src.session.astype(str))[["symbol", "session", "posted_at"]], on=["symbol", "session"])
    h = h.assign(t0=pd.to_datetime(h.t0, utc=True), halt=True)
    keep = ["symbol", "session", "t0", "halt", "cost_rt", "posted_at"]
    ev = pd.concat([f[keep], h[keep]], ignore_index=True)
    ev["posted_at"] = pd.to_datetime(ev.posted_at, utc=True)
    ev = ev[ev.session < D_STAR] if split == "train" else ev[ev.session >= D_STAR]
    return ev.sort_values(["session", "symbol", "halt"], kind="stable").reset_index(drop=True)


# --- the free feed's past bars ------------------------------------------------------------------------------------

def parse_bars(rows) -> pd.DataFrame:
    """Whole, numeric bars only (external bytes), time-ordered, at most MAX_ROWS."""
    ok = [x for x in (rows if isinstance(rows, list) else [])[:MAX_ROWS]
          if isinstance(x, dict) and isinstance(x.get("t"), str)
          and all(isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) for k in "ohlcv")]
    f = pd.DataFrame({"ts": pd.to_datetime([x["t"] for x in ok], utc=True, errors="coerce"),
                      **{n: [float(x[k]) for x in ok] for k, n in zip("ohlcv", COLS[1:])}}, columns=COLS)
    f = f.dropna(subset=["ts"])
    return f[(f[["open", "high", "low", "close"]] > 0).all(axis=1)].sort_values("ts").reset_index(drop=True)


def feed_path(symbol: str, session: str, root: Path = FEED) -> Path:
    return root / symbol / f"{session.replace('-', '')}.parquet"


def feed_bars(symbol: str, session: str, root: Path = FEED) -> pd.DataFrame | None:
    """The cached free-feed regular-hours bars of one stock-day (an empty frame = the feed had none); None = not fetched."""
    p = feed_path(symbol, session, root)
    return pd.read_parquet(p) if p.is_file() else None


def fetch_day(client, url: str, headers: dict, symbol: str, session: str) -> pd.DataFrame | None:
    """One request for one stock-day's regular session on the free feed. None on a failed request (retried later)."""
    d = date.fromisoformat(session)
    start, end = (datetime.combine(d, datetime.min.time(), ET).replace(hour=h, minute=m) for h, m in ((9, 30), (16, 0)))
    try:
        r = client.get(f"{url}/v2/stocks/{symbol}/bars", headers=headers, params={
            "timeframe": "1Min", "feed": "iex", "limit": MAX_ROWS, "adjustment": "raw",
            "start": start.isoformat(), "end": end.isoformat()})
        j = r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 - network or JSON: the day is simply asked again on the next fetch
        return None
    if not isinstance(j, dict):
        return None
    f = parse_bars(j.get("bars"))
    return f[f.ts < pd.Timestamp(end)]


# --- the book's rule on a set of bars --------------------------------------------------------------------------------

def pick(found: list[pt.Setup], f: pd.DataFrame, cut: pd.Timestamp, cost: float | None):
    """The first long setup to fill on ``f`` whose 2x-risk target beats ``cost`` (None = no cost check): (setup, trade)."""
    fills = []
    for s in found:
        if s.side != "long":
            continue
        t = pt.simulate(f, s, latest=cut)
        if t is None or (cost is not None and t.entry > 0 and pt.RR * (t.entry - s.stop) / t.entry <= cost):
            continue
        fills.append((t.entry_ts, long_book.LONG_KINDS.index(s.kind), s, t))
    return min(fills, key=lambda x: (x[0], x[1]))[2:] if fills else None


def tape_setups(f: pd.DataFrame, t0: pd.Timestamp, halt: bool) -> list[pt.Setup]:
    return pt.first_red(f, t0, "L7") if halt else pt.first_red(f, t0) + pt.bull_flag_v2(f, t0) + pt.bos_long(f, t0)


def score(tape: pd.DataFrame, s: pt.Setup, cut: pd.Timestamp, cost: float, pad: float = 0.0) -> dict | None:
    """One order on official bars with the red-candle exit: net, size, contribution. None = it does not fill there."""
    stop = s.stop * (1 - pad)
    t = pt.simulate(tape, pt.Setup(s.kind, "long", s.ready, s.trigger, stop), latest=cut, red_exit=True)
    if t is None:
        return None
    d = (t.entry - stop) / t.entry
    w = long_book.risk_weight(t.entry, stop)
    return {"net": t.gross - cost, "w": w, "c": w * (t.gross - cost), "d": d, "entry_ts": t.entry_ts, "entry": t.entry,
            "stop": stop, "reason": t.reason, "fill_bar_stop": bool(t.reason == "stop" and t.exit_ts == t.entry_ts)}


def one_event(e, tape: pd.DataFrame, feed: pd.DataFrame) -> dict:
    """All legs of one event. ``e``: symbol, session, t0, halt, cost_rt, posted_at."""
    cut, cost = latest(e.session), float(e.cost_rt)
    row = {"symbol": e.symbol, "session": e.session, "halt": bool(e.halt), "cost": cost,
           "feed_bars": int(len(feed)), "tape_bars": int(len(tape)),
           "feed_volume_share": float(feed.volume.sum() / tape.volume.sum()) if len(feed) and tape.volume.sum() > 0 else 0.0}
    # TAPE: the tested rule (and, without the cost check, the stored test's trade for the reproduction guard)
    found = tape_setups(tape, pd.Timestamp(e.t0), bool(e.halt))
    base = pick(found, tape, cut, None)
    if base is not None:
        b = score(tape, base[0], cut, cost)
        row.update(base_kind=base[0].kind, base_entry_ts=base[1].entry_ts, base_e_net=None if b is None else b["net"])
    hit = pick(found, tape, cut, cost)
    if hit is not None:
        for pad in (0.0, *PADS):
            r = score(tape, hit[0], cut, cost, pad)
            tag = "tape" if pad == 0 else f"tape_pad{pad:g}"
            if r is not None:
                row.update({f"{tag}_net": r["net"], f"{tag}_c": r["c"]})
                if pad == 0:
                    row.update(tape_kind=hit[0].kind, tape_entry_ts=r["entry_ts"], tape_stop=hit[0].stop, tape_d=r["d"],
                               tape_fill_bar_stop=r["fill_bar_stop"], tape_ready=hit[0].ready)
    # LIVE: found and picked on the free feed's bars, scored on the tape from its fill bar
    if len(feed):
        lf, start = long_shadow.setups(feed, {"posted_at": e.posted_at.isoformat(), "status": "live", "symbol": e.symbol,
                                              "session": e.session})
        row["live_halt"] = bool(start is not None and any(s.kind.startswith("L7") for s in lf))
        live = pick(lf, feed, cut, cost)
        if live is not None:
            s, t = live
            order = pt.Setup(s.kind, "long", t.entry_ts, s.trigger, s.stop)      # as long_book._score replays it
            row.update(live_kind=s.kind, live_entry_ts=t.entry_ts, live_stop=s.stop, live_ready=s.ready)
            for pad in (0.0, *PADS):
                r = score(tape, order, cut, cost, pad)
                tag = "live" if pad == 0 else f"pad{pad:g}"
                if r is None:
                    row[f"{tag}_void"] = True
                else:
                    row.update({f"{tag}_net": r["net"], f"{tag}_c": r["c"]})
                    if pad == 0:
                        row.update(live_d=r["d"], live_fill_bar_stop=r["fill_bar_stop"])
    return row


# --- numbers ---------------------------------------------------------------------------------------------------------

def paired(t: pd.DataFrame, a: str, b: str, alpha: float | None = None) -> dict:
    """Mean over ALL events of contribution(a) - contribution(b) (a leg that does not trade counts 0)."""
    d = (t[a].fillna(0.0) - t[b].fillna(0.0)).to_numpy(float)
    m = boot_mean(t.session.to_numpy(), d)
    out = {"events": int(len(d)), "mean": float(d.mean()), "lo95": float(np.quantile(m, 0.025)), "hi95": float(np.quantile(m, 0.975))}
    if alpha is not None:
        out["lower_one_sided"] = float(np.quantile(m, alpha))
    return out


def leg(t: pd.DataFrame, net: str, c: str, stopped: str | None = None) -> dict:
    x = t[t[net].notna()]
    out = {"trades": int(len(x)), "mean_net": float(x[net].mean()) if len(x) else None,
           "win": float((x[net] > 0).mean()) if len(x) else None,
           "contribution_per_event": float(t[c].fillna(0.0).mean()), "mean_size": float((x[c] / x[net]).replace([np.inf, -np.inf], np.nan).mean()) if len(x) else None}
    if stopped and len(x):
        out["stopped_on_fill_bar"] = float(x[stopped].fillna(False).astype(bool).mean())
    return out


def evaluate(t: pd.DataFrame) -> dict:
    for col in ("tape_c", "live_c", "tape_net", "live_net", *(f"pad{p:g}_{k}" for p in PADS for k in ("c", "net")),
                *(f"tape_pad{p:g}_{k}" for p in PADS for k in ("c", "net")), "live_kind", "tape_kind", "live_void",
                "live_fill_bar_stop", "tape_fill_bar_stop", "live_stop", "tape_stop", "live_halt"):
        if col not in t.columns:
            t[col] = np.nan
    res: dict = {"events": int(len(t)), "sessions": int(t.session.nunique()),
                 "no_feed_bars": int((t.feed_bars == 0).sum()),
                 "feed_volume_share": {q: float(t.feed_volume_share.quantile(v)) for q, v in (("p10", .1), ("median", .5), ("p90", .9))},
                 "legs": {"TAPE": leg(t, "tape_net", "tape_c", "tape_fill_bar_stop"), "LIVE": leg(t, "live_net", "live_c", "live_fill_bar_stop")}}
    res["legs"]["LIVE"]["voids"] = int(t.live_void.fillna(False).astype(bool).sum())
    for p in PADS:
        res["legs"][f"PAD {p:.1%}"] = leg(t, f"pad{p:g}_net", f"pad{p:g}_c")
        res["legs"][f"TAPE PAD {p:.1%}"] = leg(t, f"tape_pad{p:g}_net", f"tape_pad{p:g}_c")
    res["A_handicap"] = paired(t, "live_c", "tape_c")
    res["A_handicap"]["real"] = bool(res["A_handicap"]["hi95"] < 0)
    gate = ALPHA / len(PADS)
    res["B_pads"] = {f"{p:.1%}": paired(t, f"pad{p:g}_c", "live_c", gate) for p in PADS}
    for v in res["B_pads"].values():
        v["pass"] = bool(v.get("lower_one_sided") is not None and v["lower_one_sided"] > 0)       # no bound = fail
    res["B_pass"] = [k for k, v in res["B_pads"].items() if v["pass"]]
    res["tape_pads_vs_tape"] = {f"{p:.1%}": paired(t, f"tape_pad{p:g}_c", "tape_c") for p in PADS}
    both = t[t.live_kind.notna() & t.tape_kind.notna()]
    same = both[both.live_kind == both.tape_kind]
    gap = (same.live_stop / same.tape_stop - 1)
    res["live_vs_tape"] = {"both_trade": int(len(both)), "same_kind": int(len(same)),
                           "same_kind_and_fill_minute": int((same.live_entry_ts == same.tape_entry_ts).sum()),
                           "live_only": int((t.live_kind.notna() & t.tape_kind.isna()).sum()),
                           "tape_only": int((t.live_kind.isna() & t.tape_kind.notna()).sum()),
                           "stop_tighter_share": float((gap > 1e-9).mean()) if len(gap) else None,
                           "stop_looser_share": float((gap < -1e-9).mean()) if len(gap) else None,
                           "stop_gap_median": float(gap.median()) if len(gap) else None,
                           "stop_gap_mean": float(gap.mean()) if len(gap) else None,
                           "feed_halt_tape_not": int((t.live_halt.fillna(False).astype(bool) & ~t.halt).sum())}
    med = t.feed_volume_share.median()
    res["pads_by_feed_share"] = {name: {f"{p:.1%}": paired(part, f"pad{p:g}_c", "live_c") for p in PADS} | {
        "handicap": paired(part, "live_c", "tape_c")} for name, part in (("thin half", t[t.feed_volume_share <= med]),
                                                                          ("thick half", t[t.feed_volume_share > med])) if len(part)}
    return res


def reproduces(t: pd.DataFrame, base: pd.DataFrame) -> tuple[bool, str]:
    """TAPE without the cost check must be the stored test's trade (kind, fill bar, red-exit net) on every stored row."""
    b = base[base.split == "train"].assign(session=lambda x: x.session.astype(str), entry_ts=lambda x: pd.to_datetime(x.entry_ts, utc=True))
    m = b.merge(t[["symbol", "session", "halt", "base_kind", "base_entry_ts", "base_e_net"]], on=["symbol", "session", "halt"], how="left")
    bad = m[(m.kind != m.base_kind) | (m.entry_ts != m.base_entry_ts) | ((m.e_net - m.base_e_net).abs() > 1e-9) | m.base_kind.isna()]
    extra = int(t.base_kind.notna().sum()) - len(b)
    return (len(bad) == 0 and extra == 0), f"stored {len(b)}, mismatched {len(bad)}, extra {extra}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="only fetch and cache the free feed's bars (no number is computed)")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    res_path = a.out / "result_train.json"
    if not a.fetch and res_path.is_file():
        print(f"{res_path} exists: the training run was made once and is not repeated")
        return 2
    ev = events("train")
    if a.fetch:
        import httpx

        from sgt.config import get_settings
        from sgt.intraday.sources import _alpaca_headers

        url, n, failed = get_settings().alpaca_data_url.rstrip("/"), 0, 0
        with httpx.Client(timeout=30) as client:
            for sym, session in ev[["symbol", "session"]].drop_duplicates().itertuples(index=False):
                p = feed_path(sym, session)
                if p.is_file():
                    continue
                f = fetch_day(client, url, _alpaca_headers(), sym, session)
                time.sleep(THROTTLE_S)
                if f is None:
                    failed += 1
                    continue
                p.parent.mkdir(parents=True, exist_ok=True)
                f.to_parquet(p)
                n += 1
                if n % 250 == 0:
                    print(f"fetched {n} stock-days", flush=True)
        print(f"done: {n} fetched, {failed} failed (run again to retry), {len(ev)} events", flush=True)
        return 0 if not failed else 1
    bars, rows, missing = Bars(), [], 0
    for e in ev.itertuples(index=False):
        tape = bars(e.symbol, e.session)
        feed = feed_bars(e.symbol, e.session)
        if feed is None:
            missing += 1
            continue
        if not len(tape):
            continue
        rows.append(one_event(e, tape, feed))
    if missing:
        raise SystemExit(f"{missing} event days have no cached free-feed bars: run --fetch first; nothing reported")
    t = pd.DataFrame(rows)
    ok, why = reproduces(t, pd.read_csv(BASE, converters={"symbol": str}))
    if not ok:
        raise SystemExit(f"TAPE does not reproduce the stored test ({why}): nothing reported")
    res = {"prereg": "docs/findings/prereg_long_feed_replay_2026-11-18.md", "reproduction": why, **evaluate(t)}
    a.out.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1, default=str) + "\n")
    t.to_csv(a.out / "train_events.csv", index=False)
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
