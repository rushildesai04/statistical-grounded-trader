"""Opening-range breakout on the day's stocks in play (prereg_orb_stocks_in_play_2026-11-05).

    python scripts/orb_sip_study.py fetch --start 2021-01-04 --end 2021-03-31   # training data, resumable
    python scripts/orb_sip_study.py dev                                         # training only, REPORT ONLY
    python scripts/orb_sip_study.py holdout                                     # ONE-SHOT vault unlock (do not run lightly)

Data layout under ``data/orb_sip/`` (all resumable; an existing file is complete):
    assets.json                              # us_equity assets on NYSE/NASDAQ/AMEX, active+inactive
    daily/<start>_<end>/batch_XXXX.parquet   # split-adjusted 1Day bars, per symbol batch
    open/date=YYYY-MM-DD.parquet             # 09:30-09:44 1-min window for the eligible universe
    session/date=YYYY-MM-DD.parquet          # full-RTH 1-min bars for the day's top-20

The RVOL universe pass and daily pass are the universe-wide work done by ``fetch``; the
top-20 full-session bars are fetched on demand inside ``dev``/``holdout`` (they depend on
the selection). ``fetch`` refuses held-out dates: held-out data is downloaded only inside
the unlocked ``holdout`` command (crypto_momentum_study.py pattern).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.config import get_settings
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision.features import CostModelAdapter
from sgt.intraday.calendar import ET, default_calendar
from sgt.intraday.sources import _alpaca_headers
from sgt.research import orb_sip
from sgt.research.vault import Vault, load_registry

log = logging.getLogger("orb_sip")

STUDY = "orb-sip-2026-11"
PREREG = "docs/findings/prereg_orb_stocks_in_play_2026-11-05.md"
SCORER = "scripts/orb_sip_study.py"
ROOT = Path("data/orb_sip")
OUT = Path("reports/orb_sip")
HOLDOUT_HALVES_SPLIT = pd.Timestamp("2025-06-01")        # prereg gate split
PAD_SESSIONS = 25                                        # prior sessions needed for ADV/ATR(15)/RVOL(14)

DAILY_BATCH = 200
OPEN_BATCH = 120
TIMEOUT_S = 30.0
RETRIES = 6
PACE_S = 0.34                                            # ~180 req/min
MAX_PAGES = 400
MAX_BYTES = 60_000_000
EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}


# --------------------------------------------------------------------------- HTTP (hostile bytes)
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z]*([.\-/][A-Z]+)*$")        # ticker, optional class/warrant suffix
_INVALID_SYMBOL_RE = re.compile(r"invalid symbol:\s*([^\"\s]+)")


class AlpacaHTTPError(RuntimeError):
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"HTTP {status}: {text[:200]}")
        self.status = status
        self.text = text


def _data_client() -> httpx.Client:
    return httpx.Client(base_url=get_settings().alpaca_data_url.rstrip("/"),
                        headers=_alpaca_headers(), timeout=TIMEOUT_S)


def _get(client: httpx.Client, path: str, params: dict, sleep=time.sleep) -> dict:
    """One GET with bounded retries/backoff on 429/5xx/network. Returns parsed JSON (bounded)."""
    for attempt in range(RETRIES):
        try:
            r = client.get(path, params=params)
        except httpx.TransportError as exc:
            log.warning("%s network error %s (attempt %d)", path, type(exc).__name__, attempt + 1)
            sleep(2 ** attempt)
            continue
        if r.status_code == 429 or r.status_code >= 500:
            sleep(2 ** attempt)
            continue
        if r.status_code >= 400:
            raise AlpacaHTTPError(r.status_code, r.text)
        if len(r.content) > MAX_BYTES:
            raise RuntimeError(f"{path} response too large ({len(r.content)} bytes)")
        return r.json()
    raise RuntimeError(f"{path} failed after {RETRIES} attempts")


def _bars_multi(client: httpx.Client, symbols: list[str], timeframe: str, start: datetime,
                end: datetime, adjustment: str, sleep=time.sleep) -> dict[str, list]:
    """Paginated /v2/stocks/bars for several symbols. Returns {symbol: [raw bar dicts]}.

    Alpaca rejects the whole request with 400 if any symbol is unknown to the data API
    (common for inactive/placeholder assets); such symbols are dropped and the batch is
    retried without them (bounded), so one dead name never blocks a batch."""
    syms = list(symbols)
    params = {"symbols": ",".join(syms), "timeframe": timeframe, "start": start.isoformat(),
              "end": end.isoformat(), "feed": "sip", "adjustment": adjustment, "limit": 10000,
              "sort": "asc"}
    out: dict[str, list] = {}
    for _ in range(MAX_PAGES):
        try:
            j = _get(client, "/v2/stocks/bars", params, sleep)
        except AlpacaHTTPError as exc:
            m = exc.status == 400 and _INVALID_SYMBOL_RE.search(exc.text)
            if not m:
                raise
            bad = m.group(1)
            if bad not in syms:
                raise
            syms.remove(bad)
            if not syms:
                return out
            params["symbols"] = ",".join(syms)
            params.pop("page_token", None)
            out.clear()
            continue
        bars = j.get("bars") if isinstance(j, dict) else None
        if isinstance(bars, dict):
            for s, rows in bars.items():
                if isinstance(rows, list):
                    out.setdefault(s, []).extend(rows)
        tok = j.get("next_page_token") if isinstance(j, dict) else None
        sleep(PACE_S)
        if not tok:
            break
        params["page_token"] = tok
    else:
        raise RuntimeError("bars pagination did not finish within MAX_PAGES")
    return out


def _rows_to_daily(sym: str, rows: list) -> pd.DataFrame:
    recs, bad = [], 0
    for x in rows:
        if not isinstance(x, dict):
            bad += 1
            continue
        try:
            t = pd.Timestamp(x["t"])
            o, h, low, c, v = float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"])
        except (KeyError, TypeError, ValueError):
            bad += 1
            continue
        if not (np.isfinite([o, h, low, c, v]).all() and 0 < low <= min(o, c) and max(o, c) <= h and v >= 0):
            bad += 1
            continue
        recs.append({"symbol": sym, "date": t.tz_convert(ET).date(), "open": o, "high": h,
                     "low": low, "close": c, "volume": v})
    if bad:
        log.warning("%s: dropped %d malformed daily bars", sym, bad)
    return pd.DataFrame(recs, columns=list(orb_sip._DAILY_COLS))


def _rows_to_minute(sym: str, rows: list) -> pd.DataFrame:
    recs, bad = [], 0
    for x in rows:
        if not isinstance(x, dict):
            bad += 1
            continue
        try:
            t = pd.Timestamp(x["t"])
            o, h, low, c, v = float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x["v"])
        except (KeyError, TypeError, ValueError):
            bad += 1
            continue
        if not (np.isfinite([o, h, low, c, v]).all() and 0 < low <= min(o, c) and max(o, c) <= h and v >= 0):
            bad += 1
            continue
        et = t.tz_convert(ET)
        recs.append({"symbol": sym, "date": et.date(), "minute": et.hour * 60 + et.minute,
                     "ts": t.tz_convert("UTC"), "open": o, "high": h, "low": low, "close": c, "volume": v})
    if bad:
        log.warning("%s: dropped %d malformed minute bars", sym, bad)
    return pd.DataFrame(recs, columns=["symbol", "date", "minute", "ts", "open", "high", "low", "close", "volume"])


# --------------------------------------------------------------------------- universe + calendar
def _sessions(start: date, end: date) -> list[date]:
    cal = default_calendar()
    pad = cal.previous_sessions(start, PAD_SESSIONS)
    out = list(pad)
    d = start
    while d <= end:
        if cal.is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return sorted(set(out))


def load_assets(client_t: httpx.Client | None = None, sleep=time.sleep) -> list[str]:
    """US-equity symbols on NYSE/NASDAQ/AMEX (active and inactive), cached to assets.json."""
    p = ROOT / "assets.json"
    if p.exists():
        return json.loads(p.read_text())
    headers = _alpaca_headers()
    base = get_settings().alpaca_paper_url.rstrip("/")
    with (client_t or httpx.Client(base_url=base, headers=headers, timeout=TIMEOUT_S)) as c:
        j = _get(c, "/v2/assets", {"asset_class": "us_equity", "status": "all"}, sleep)
    if not isinstance(j, list):
        raise RuntimeError("assets response is not a list")
    syms = sorted({a["symbol"] for a in j if isinstance(a, dict) and a.get("class") == "us_equity"
                   and a.get("exchange") in EXCHANGES and isinstance(a.get("symbol"), str)
                   and _SYMBOL_RE.match(a["symbol"])})       # drop CUSIP-style placeholder assets
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(syms))
    log.info("assets: %d symbols on %s", len(syms), "/".join(sorted(EXCHANGES)))
    return syms


# --------------------------------------------------------------------------- fetch stages
def fetch_daily(client: httpx.Client, symbols: list[str], start: date, end: date, sleep=time.sleep) -> pd.DataFrame:
    """Split-adjusted daily bars for the universe over [start, end]. Resumable per symbol batch."""
    tag = f"{start.isoformat()}_{end.isoformat()}"
    d = ROOT / "daily" / tag
    d.mkdir(parents=True, exist_ok=True)
    s = datetime.combine(start, datetime.min.time(), tzinfo=ET)
    e = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=ET)
    batches = [symbols[i:i + DAILY_BATCH] for i in range(0, len(symbols), DAILY_BATCH)]
    for i, batch in enumerate(batches):
        f = d / f"batch_{i:04d}.parquet"
        if f.exists():
            continue
        raw = _bars_multi(client, batch, "1Day", s, e, "split", sleep)
        frames = [_rows_to_daily(sym, rows) for sym, rows in raw.items()]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=list(orb_sip._DAILY_COLS))
        df.to_parquet(f.with_suffix(".tmp"), index=False)
        f.with_suffix(".tmp").replace(f)
        log.info("daily batch %d/%d: %d symbols, %d rows", i + 1, len(batches), len(batch), len(df))
    parts = [pd.read_parquet(f) for f in sorted(d.glob("batch_*.parquet"))]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=list(orb_sip._DAILY_COLS))


def fetch_open_windows(client: httpx.Client, eligible: list[str], sessions: list[date], sleep=time.sleep) -> None:
    """09:30-09:44 1-min window for the eligible universe, one parquet per session. Resumable."""
    cal = default_calendar()
    d = ROOT / "open"
    d.mkdir(parents=True, exist_ok=True)
    for sess in sessions:
        f = d / f"date={sess.isoformat()}.parquet"
        if f.exists() or not cal.is_session(sess):
            continue
        b = cal.bounds(sess)
        s, e = b.rth_open, b.rth_open + timedelta(minutes=15)
        frames = []
        for i in range(0, len(eligible), OPEN_BATCH):
            raw = _bars_multi(client, eligible[i:i + OPEN_BATCH], "1Min", s, e, "raw", sleep)
            frames.extend(_rows_to_minute(sym, rows) for sym, rows in raw.items())
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
            columns=["symbol", "date", "minute", "ts", "open", "high", "low", "close", "volume"])
        df = df[(df["minute"] >= orb_sip.OPEN_MIN) & (df["minute"] <= orb_sip.OR15_LAST_MIN)]
        df.to_parquet(f.with_suffix(".tmp"), index=False)
        f.with_suffix(".tmp").replace(f)
        log.info("open window %s: %d symbols with bars", sess, df["symbol"].nunique())


def fetch_session_bars(client: httpx.Client, sess: date, symbols: list[str], sleep=time.sleep) -> pd.DataFrame:
    """Full-RTH 1-min bars (raw) for ``symbols`` on one session, cached per date. Resumable."""
    d = ROOT / "session"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"date={sess.isoformat()}.parquet"
    if f.exists():
        return pd.read_parquet(f)
    cal = default_calendar()
    b = cal.bounds(sess)
    raw = _bars_multi(client, symbols, "1Min", b.rth_open, b.rth_close, "raw", sleep)
    frames = [_rows_to_minute(sym, rows) for sym, rows in raw.items()]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["symbol", "date", "minute", "ts", "open", "high", "low", "close", "volume"])
    df = df[df["minute"] < orb_sip.OPEN_MIN + 390]       # RTH only
    df.to_parquet(f.with_suffix(".tmp"), index=False)
    f.with_suffix(".tmp").replace(f)
    return df


# --------------------------------------------------------------------------- selection + trades
def eligible_universe(filters: pd.DataFrame, sessions: list[date]) -> list[str]:
    """Symbols passing the daily filters on at least one session in range (needs their opening
    windows on all prior sessions too, so the window pass covers every session for this set)."""
    ss = set(sessions)
    passed = filters[filters["passes"] & filters["date"].isin(ss)]
    return sorted(passed["symbol"].unique())


def build_trades(filters: pd.DataFrame, rvol: pd.DataFrame, range_agg: pd.DataFrame, trade_days: list[date],
                 fetch_session, adapter, entry_min: int) -> pd.DataFrame:
    """Walk trading days: select top-20 stocks in play (ranked by the 5-bar RVOL in ``rvol``),
    then break out of the opening range in ``range_agg`` with entry live from ``entry_min``.
    The primary pass uses the 5-bar range + 09:35 entry; the 15-min secondary uses the 15-bar
    range + 09:45 entry. ``fetch_session(sess, symbols)`` returns the day's RTH minute frame."""
    cal = default_calendar()
    rows: list[dict] = []
    fkey = filters.set_index(["symbol", "date"])
    for sess in trade_days:
        if not cal.is_session(sess):
            continue
        day_f = filters[filters["date"] == sess]
        day_r = rvol[rvol["date"] == sess]
        day_a = range_agg[range_agg["date"] == sess]
        merged = day_f.merge(day_r[["symbol", "rvol", "nsess"]], on="symbol", how="inner")
        merged = merged.merge(day_a[["symbol", "or_open", "or_close", "or_high", "or_low"]],
                              on="symbol", how="inner")
        merged = merged[np.isfinite(merged["or_open"].to_numpy(float))]
        sel = orb_sip.select_in_play(merged, orb_sip.TOP_N)
        if len(sel) == 0:
            continue
        bars_all = fetch_session(sess, sorted(sel["symbol"].tolist()))
        for r in sel.itertuples(index=False):
            side = orb_sip.direction(r.or_open, r.or_close)
            if side == 0:
                continue
            atr = float(fkey.loc[(r.symbol, sess), "atr14"])
            if not np.isfinite(atr):
                continue
            sb = bars_all[(bars_all["symbol"] == r.symbol) & (bars_all["minute"] >= entry_min)]
            sb = sb.sort_values("minute")
            rec = orb_sip.trade_record(r.symbol, sess, side, int(r.rank), float(r.or_high),
                                       float(r.or_low), float(r.or_open), atr, sb, adapter)
            if rec is not None:
                rows.append(rec)
    cols = ["date", "symbol", "side", "rank", "atr14", "or_open", "or_high", "or_low", "entry", "exit",
            "stopped", "entry_ts", "gross", "net_primary", "net_commission_only", "net_half_spread",
            "gross_entrybar_free", "net_commission_only_entrybar_free", "cost_primary", "stop_dist"]
    return pd.DataFrame(rows, columns=cols)


def _print_summary(name: str, s: dict, port: dict) -> None:
    if s.get("n", 0) == 0:
        print(f"{name}: no trades")
        return
    print(f"{name}: n {s['n']} gross {s['mean_gross']:+.3%} net {s['mean_net']:+.3%} "
          f"hit {s['hit']:.0%} stopped {s['stopped_share']:.0%} lower95 {s['lower95']:+.3%}")
    print("  costs: " + "  ".join(f"{k} {v:+.3%}" for k, v in s["costs"].items()))
    print("  by_side: " + "  ".join(
        f"{'long' if k > 0 else 'short'} n{v['n']} net{v['mean_net']:+.3%}" for k, v in s["by_side"].items()))
    print("  tops: " + "  ".join(f"{k} {v:+.3%}" for k, v in s["tops"].items()))
    print(f"  halves {tuple(round(h, 5) for h in s['halves'])}  top_month_share {s['top_month_share']:.2f}")
    print("  per_year: " + "  ".join(f"{y} n{v['n']} net{v['mean_net']:+.3%}" for y, v in s["per_year"].items()))
    print(f"  portfolio: final ${port['final_equity']:,.0f}  ret {port['total_return']:+.1%} "
          f"maxDD {port['max_dd']:.1%} over {port['days']}d")
    print(f"VERDICT ({name}): {'PASS' if s['pass'] else 'FAIL/report-only'}")


def run(name: str, trades: pd.DataFrame, trades15: pd.DataFrame, halves_split) -> dict:
    s = orb_sip.summarize(trades, halves_split) if len(trades) else {"n": 0, "pass": False}
    port = orb_sip.portfolio_sim(trades)
    s15 = orb_sip.summarize(trades15, halves_split) if len(trades15) else {"n": 0, "pass": False}
    ebf = float(trades["net_commission_only_entrybar_free"].mean()) if len(trades) else None
    res = {"primary": s, "portfolio": port, "secondary_entrybar_free_commission_only": ebf,
           "secondary_15min": {"n": s15.get("n", 0), "mean_net": s15.get("mean_net"),
                               "hit": s15.get("hit"), "lower95": s15.get("lower95")}}
    _print_summary(name, s, port)
    if ebf is not None:
        print(f"  entry-bar stop ignored (A1 bound), commission only: {ebf:+.3%}")
    if s15.get("n", 0):
        print(f"  15-min range: n {s15['n']} net {s15['mean_net']:+.3%} hit {s15['hit']:.0%}")
    return res


def save(kind: str, res: dict, trades: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{kind}.json").write_text(json.dumps(res, indent=2, default=str))
    trades.to_csv(OUT / f"{kind}_trades.csv", index=False)
    init_db()
    with get_session() as sdb:
        trial_ledger.record_trial(sdb, run_id=f"orb-sip-2026-11-05-{kind}", kind=f"orb-sip-{kind}",
                                  params={"prereg": PREREG}, metrics=json.loads(json.dumps(res, default=str)))


# --------------------------------------------------------------------------- pipeline (shared by dev/holdout)
def _prepare(start: date, end: date, client: httpx.Client, fetch_universe: bool) -> tuple:
    """Load cached daily + opening windows, compute filters/agg/rvol. Optionally run the
    universe fetch first (dev pre-fetches via the fetch command; holdout fetches here)."""
    sessions = _sessions(start, end)
    if fetch_universe:
        symbols = load_assets()
        daily = fetch_daily(client, symbols, sessions[0], end)
        filters = orb_sip.build_daily_filters(daily)
        fetch_open_windows(client, eligible_universe(filters, sessions), sessions)
    daily = _load_daily(sessions[0], end)
    filters = orb_sip.build_daily_filters(daily)
    allbars = _load_open_bars(sessions)
    agg5 = orb_sip.aggregate_opening(allbars, last_min=orb_sip.OR5_LAST_MIN)
    agg15 = orb_sip.aggregate_opening(allbars, last_min=orb_sip.OR15_LAST_MIN)
    rvol = orb_sip.opening_rvol(agg5, sessions)            # RVOL is always the 5-bar window
    return filters, agg5, agg15, rvol, sessions


def _load_daily(start: date, end: date) -> pd.DataFrame:
    tag = f"{start.isoformat()}_{end.isoformat()}"
    d = ROOT / "daily" / tag
    if not d.exists():
        raise SystemExit(f"no cached daily bars for {tag}; run `fetch --start {start} --end {end}` first")
    parts = [pd.read_parquet(f) for f in sorted(d.glob("batch_*.parquet"))]
    if not parts:
        raise SystemExit(f"daily cache {d} is empty")
    return pd.concat(parts, ignore_index=True)


def _load_open_bars(sessions: list[date]) -> pd.DataFrame:
    d = ROOT / "open"
    parts = []
    for sess in sessions:
        f = d / f"date={sess.isoformat()}.parquet"
        if f.exists():
            df = pd.read_parquet(f)
            if len(df):
                parts.append(df)
    if not parts:
        raise SystemExit("no cached opening windows; run `fetch` first")
    return pd.concat(parts, ignore_index=True)


# --------------------------------------------------------------------------- commands
def cmd_fetch(args) -> int:
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    ho_start = load_registry()[STUDY].holdout[0]
    if end >= ho_start:
        raise SystemExit(f"fetch refuses held-out dates: end {end} reaches holdout from {ho_start}; "
                         "held-out data is downloaded only inside the unlocked `holdout` command")
    sessions = _sessions(start, end)
    with _data_client() as client:
        symbols = load_assets()
        daily = fetch_daily(client, symbols, sessions[0], end)
        filters = orb_sip.build_daily_filters(daily)
        elig = eligible_universe(filters, sessions)
        log.info("eligible universe: %d symbols", len(elig))
        fetch_open_windows(client, elig, sessions)
    print(f"fetch complete: {len(symbols)} assets, daily {sessions[0]}..{end}, opening windows for "
          f"{len(sessions)} sessions")
    return 0


def _trade_days(start: date, end: date) -> list[date]:
    cal = default_calendar()
    out, d = [], start
    while d <= end:
        if cal.is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def cmd_dev(args) -> int:
    study = load_registry()[STUDY]
    start = date.fromisoformat(args.start) if args.start else study.train[0]
    end = date.fromisoformat(args.end) if args.end else study.train[1]
    if end >= study.holdout[0]:                          # dev is training-only, always
        raise SystemExit(f"dev end {end} reaches held-out data from {study.holdout[0]}")
    filters, agg5, agg15, rvol, sessions = _prepare(start, end, None, fetch_universe=False)
    adapter = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                               rest_mult=GAPPER_REST_MULT)
    trade_days = _trade_days(start, end)
    with _data_client() as client:
        def fs(sess, syms):
            return fetch_session_bars(client, sess, syms)
        trades = build_trades(filters, rvol, agg5, trade_days, fs, adapter, orb_sip.ENTRY5_FIRST_MIN)
        trades15 = build_trades(filters, rvol, agg15, trade_days, fs, adapter, orb_sip.ENTRY15_FIRST_MIN)
    split = pd.to_datetime(trades["date"]).median() if len(trades) else start
    res = run("DEV (training, report-only)", trades, trades15, split)
    save("dev", res, trades)
    return 0


def cmd_holdout(args) -> int:
    head = Vault(STUDY).unlock(scorer=SCORER)             # one-shot, recorded, needs a clean commit
    print(f"vault {STUDY} unlocked at {head}")
    study = load_registry()[STUDY]
    start, end = study.holdout
    adapter = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                               rest_mult=GAPPER_REST_MULT)
    with _data_client() as client:
        filters, agg5, agg15, rvol, sessions = _prepare(start, end, client, fetch_universe=True)
        trade_days = _trade_days(start, end)
        # fail closed: every trading day must have an opening-window file before any return is computed
        missing = [d for d in trade_days if not (ROOT / "open" / f"date={d.isoformat()}.parquet").exists()]
        if missing:
            raise SystemExit(f"{len(missing)} trading days missing opening windows (e.g. {missing[0]}); "
                             "nothing scored. Re-run via the vault's rescore_reason.")
        def fs(sess, syms):
            return fetch_session_bars(client, sess, syms)
        trades = build_trades(filters, rvol, agg5, trade_days, fs, adapter, orb_sip.ENTRY5_FIRST_MIN)
        trades15 = build_trades(filters, rvol, agg15, trade_days, fs, adapter, orb_sip.ENTRY15_FIRST_MIN)
    res = run("HELD-OUT (decisive)", trades, trades15, HOLDOUT_HALVES_SPLIT)
    save("holdout", res, trades)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--start", required=True)
    f.add_argument("--end", required=True)
    d = sub.add_parser("dev")
    d.add_argument("--start", default=None)
    d.add_argument("--end", default=None)
    sub.add_parser("holdout")
    args = ap.parse_args()
    return {"fetch": cmd_fetch, "dev": cmd_dev, "holdout": cmd_holdout}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
