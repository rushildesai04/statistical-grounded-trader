"""The daily model's long calls on 80 large caps it has never seen (docs/findings/prereg_fresh_names_2026-11-17.md).

    .venv/bin/python scripts/fresh_names_study.py --universe     # write the list (no price of a fresh name is read)
    .venv/bin/python scripts/fresh_names_study.py --fetch        # 15-minute bars + adjusted daily bars (cached)
    .venv/bin/python scripts/fresh_names_study.py                # training, run ONCE
    .venv/bin/python scripts/fresh_names_study.py --holdout      # only after the training PASS, once
    .venv/bin/python scripts/fresh_names_study.py --bridge       # descriptive: the 21 known names from 15-minute bars

Report only: nothing here changes a book. External bytes (the bar API) are parsed strictly, bounded in pages and time.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import long_rules_study as base
from megacap_daily_study import PARAMS, WF_START
from megacap_study import CACHE, END, START, adjusted_daily
from sgt.megacap import rules, score
from sgt.megacap.daily import DAILY_FEATURES, build_daily
from sgt.megacap.walkforward import walk_forward

ET = "America/New_York"
OUT = Path("reports/fresh_names")
DAILY_DIR = Path("data/daily")
TRAIN, HOLD = base.TRAIN, base.HOLD
N_NAMES, RANK_YEAR, MIN_RANK_SESSIONS = 80, "2019", 200
MIN_M15_SESSIONS = 1000
MIN_SCORED, MIN_CALLS = 60, 5000
ALPHA, SEED, N_BOOT = 0.05 / 2, 20261117, 10_000
COST = score.COST
BAR_MIN, GRID = 15, 390
# The API pages on the underlying minutes: one page is about five weeks of quarter-hours (about 860 bars), so eleven
# years is about 115 pages. Requests are spaced to stay well under the account's 200 a minute (the runner shares it).
MAX_PAGES, PAGE_LIMIT, TIMEOUT_S, MAX_RETRIES, MIN_INTERVAL_S = 220, 10_000, 30.0, 6, 0.4
MAX_BARS = 400_000                       # per symbol: 11 years x 252 sessions x 64 quarter-hours is about 177,000
FUNDS = frozenset("""SPY QQQ IWM EEM EFA GDX XLF HYG GLD IVV TLT FXI TQQQ LQD XLK XLU XLV EWZ XLI DIA VXX VOO XLE XLP IEMG
SMH IYR GDXJ XOP VWO XLY XBI IEF AGG IEFA VNQ VTI EWJ EMB MDY UVXY SQQQ KRE XLB USO VEA""".split())
SEEN = ("AAPL AMZN GOOGL MSFT META NVDA TSLA RDDT AVGO NFLX COST JPM V MA LLY UNH XOM WMT ORCL AMD AAOI").split()
SECOND_LISTINGS = frozenset({"FB", "GOOG"})


# --- universe ---------------------------------------------------------------------------------------------------------
def rank(daily_dir: Path = DAILY_DIR) -> pd.DataFrame:
    """Every symbol with >= MIN_RANK_SESSIONS sessions in RANK_YEAR: median dollar volume that year and the last date
    of its daily history. Reads date, close and volume only."""
    import pyarrow.parquet as pq

    rows = []
    for p in sorted(daily_dir.glob("*.parquet")):
        try:
            d = pq.read_table(p, columns=["date", "close", "volume"]).to_pandas()
        except (OSError, ValueError, KeyError):
            continue
        dt = d.date.astype(str).str[:10]
        m = dt.str.startswith(RANK_YEAR)
        if int(m.sum()) < MIN_RANK_SESSIONS:
            continue
        rows.append((p.stem, float((d.close[m] * d.volume[m]).median()), str(dt.max())))
    r = pd.DataFrame(rows, columns=["symbol", "dv", "last"])
    return r.sort_values(["dv", "symbol"], ascending=[False, True], kind="stable").reset_index(drop=True)


def pick(r: pd.DataFrame, n: int = N_NAMES) -> list[str]:
    """The first ``n`` ranked symbols that are not a fund, not already modelled (or its second listing) and whose
    daily history reaches the test window."""
    gone = FUNDS | set(SEEN) | SECOND_LISTINGS
    keep = r[~r.symbol.isin(gone) & (r["last"] >= TRAIN[0])]
    return keep.symbol.head(n).tolist()


# --- bars -------------------------------------------------------------------------------------------------------------
def parse_bars(rows: list) -> pd.DataFrame:
    """API rows -> ts (UTC), open, high, low, close, volume. Any malformed row raises: nothing is guessed."""
    if not isinstance(rows, list) or len(rows) > MAX_BARS:
        raise ValueError("bars: not a list or too many rows")
    df = pd.DataFrame({"ts": pd.to_datetime([r["t"] for r in rows], utc=True),
                       **{name: np.asarray([float(r[k]) for r in rows], float)
                          for name, k in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v"))}})
    px = df[["open", "high", "low", "close"]].to_numpy(float)
    if len(df) and (not np.isfinite(px).all() or (px <= 0).any() or (df.high < df.low).any() or (df.volume < 0).any()):
        raise ValueError("bars: bad price or volume")
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def regular_hours(df: pd.DataFrame) -> pd.DataFrame:
    et = df.ts.dt.tz_convert(ET)
    k = et.dt.hour * 60 + et.dt.minute - (9 * 60 + 30)
    return df[(k >= 0) & (k < GRID) & (k % BAR_MIN == 0)].reset_index(drop=True)


def fetch_m15(sym: str, start: date = START, end: date = END) -> pd.DataFrame:
    """15-minute SIP bars (raw prices), regular hours only; cached under data/megacap/m15_<SYM>.parquet."""
    import httpx

    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    path = CACHE / f"m15_{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars"
    params = {"symbols": sym, "timeframe": "15Min", "start": f"{start.isoformat()}T00:00:00Z",
              "end": f"{end.isoformat()}T23:59:59Z", "adjustment": "raw", "feed": "sip", "limit": PAGE_LIMIT}
    rows: list = []
    with httpx.Client(timeout=TIMEOUT_S) as client:
        for _ in range(MAX_PAGES):
            for attempt in range(MAX_RETRIES + 1):
                time.sleep(MIN_INTERVAL_S)
                try:
                    r = client.get(url, params=params, headers=_alpaca_headers())
                except httpx.TransportError:
                    r = None
                if r is not None and r.status_code == 200:
                    break
                if attempt == MAX_RETRIES or (r is not None and r.status_code not in (429, 500, 502, 503, 504)):
                    raise RuntimeError(f"{sym}: bar request failed ({'transport' if r is None else r.status_code})")
                time.sleep(min(30.0, 2.0 * 2 ** attempt))
            j = r.json()
            got = (j.get("bars") or {}).get(sym) or []
            if not isinstance(got, list):
                raise ValueError(f"{sym}: bars is not a list")
            rows += got
            token = j.get("next_page_token")
            if not token:
                break
            params["page_token"] = str(token)[:512]
        else:
            raise RuntimeError(f"{sym}: more than {MAX_PAGES} pages")
    df = regular_hours(parse_bars(rows))
    CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df


def agg15(bars: pd.DataFrame) -> pd.DataFrame:
    """Regular-hours 1-minute bars -> 15-minute bars stamped at the bar's start (first open, extremes, last close)."""
    b = bars[["ts", "open", "high", "low", "close", "volume"]].copy()
    b["ts"] = pd.to_datetime(b.ts, utc=True)
    et = b.ts.dt.tz_convert(ET)
    k = et.dt.hour * 60 + et.dt.minute - (9 * 60 + 30)
    b = b[(k >= 0) & (k < GRID)].sort_values("ts")
    g = b.groupby(b.ts.dt.floor(f"{BAR_MIN}min"), sort=True)
    out = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                volume=("volume", "sum")).reset_index()
    return out


def expand(m15: pd.DataFrame) -> pd.DataFrame:
    """15-minute bars -> 1-minute rows for ``build_daily``: the bar's open in each of its first 14 minutes, the full
    bar in its last minute. A value read at minute k was therefore printed by the last quarter-hour mark at or
    before k (no bar is visible before it has ended)."""
    n = len(m15)
    step = np.arange(BAR_MIN)
    start = pd.DatetimeIndex(pd.to_datetime(m15.ts, utc=True)).tz_localize(None).to_numpy("datetime64[ns]")
    ts = np.repeat(start, BAR_MIN) + np.tile(step, n).astype("timedelta64[m]")
    last = np.tile(step == BAR_MIN - 1, n)
    o = np.repeat(m15.open.to_numpy(float), BAR_MIN)
    pick_ = lambda col: np.where(last, np.repeat(m15[col].to_numpy(float), BAR_MIN), o)  # noqa: E731
    return pd.DataFrame({"ts": pd.DatetimeIndex(ts, tz="UTC"), "open": o, "high": pick_("high"), "low": pick_("low"),
                         "close": pick_("close"), "volume": np.where(last, np.repeat(m15.volume.to_numpy(float), BAR_MIN), 0.0)})


def market_m15(sym: str) -> pd.DataFrame:
    """SPY / QQQ quarter-hour bars from the local 1-minute store (cached)."""
    path = CACHE / f"m15_{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    from sgt.intraday.store import ParquetBarStore

    df = agg15(ParquetBarStore("data/alpaca").read_frame(sym, START, END))
    df.to_parquet(path)
    return df


def features(sym: str, tag: str = "dfeat15") -> pd.DataFrame | None:
    """The frozen daily feature rows of one name from quarter-hour bars (cached); None with too little history."""
    path = CACHE / f"{tag}_{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    m15 = pd.read_parquet(CACHE / f"m15_{sym}.parquet")
    if m15.ts.dt.tz_convert(ET).dt.date.nunique() < MIN_M15_SESSIONS:
        return None
    spy, qqq = (expand(pd.read_parquet(CACHE / f"m15_{s}.parquet")) for s in ("SPY", "QQQ"))
    d = build_daily(expand(m15), adjusted_daily(sym), spy, adjusted_daily("SPY"), qqq, adjusted_daily("QQQ"))
    d.to_parquet(path)
    return d


def predict(job: tuple[str, str, str]) -> tuple[str, pd.DataFrame | None]:
    """Walk-forward record of one name on rows up to ``last`` (the held-out months are not even predicted in a
    training run)."""
    sym, tag, last = job
    d = features(sym, tag)
    if d is None or d.empty:
        return sym, None
    d = d[d.session.astype(str) <= last]
    t = walk_forward(d, WF_START, features=list(DAILY_FEATURES), params=PARAMS, embargo_sessions=1)
    if t.empty:
        return sym, None
    t = t.assign(symbol=sym, session=t.session.astype(str))
    t["position"] = t.position.where(t.position > 0, 0).astype(int)          # long calls only
    return sym, t[["session", "symbol", "label", "pred", "position", "d_rv21"]]


# --- decision ---------------------------------------------------------------------------------------------------------
def ledger(t: pd.DataFrame, cost: float = COST) -> pd.Series:
    """D1: per session, the sum of (return - cost) over the long calls divided by the names with a row that day."""
    net = (t.label - cost).where(t.position > 0, 0.0)
    return net.groupby(t.session).sum() / t.groupby("session").size()


def excess(t: pd.DataFrame) -> pd.Series:
    """D2: per session with a long call, mean return of the long calls minus the mean return of all names."""
    calls = t[t.position > 0]
    return (calls.groupby("session").label.mean() - t.groupby("session").label.mean()).dropna()


def boot(d: np.ndarray) -> tuple[float, float, float]:
    """(mean, one-sided lower bound at ALPHA, p) by session bootstrap; p = (1 + resamples <= 0) / (n + 1)."""
    if len(d) == 0:
        return float("nan"), float("nan"), 1.0
    idx = np.random.default_rng(SEED).integers(0, len(d), size=(N_BOOT, len(d)))
    boots = d[idx].mean(1)
    return float(d.mean()), float(np.quantile(boots, ALPHA)), float((1 + (boots <= 0).sum()) / (N_BOOT + 1))


def evaluate(t: pd.DataFrame, window: tuple[str, str], split: str, dropped: list[str]) -> dict:
    t = t[(t.session >= window[0]) & (t.session <= window[1])].dropna(subset=["label", "pred"])
    calls = t[t.position > 0].assign(net=lambda x: x.label - COST)
    led, exc = ledger(t), excess(t)
    m1, lo1, p1 = boot(led.to_numpy(float))
    m2, lo2, p2 = boot(exc.to_numpy(float))
    names = int(t.symbol.nunique())
    out = {"split": split, "window": list(window), "names": names, "dropped": dropped,
           "sessions": int(t.session.nunique()), "rows": int(len(t)), "calls": base._calls(calls), "alpha": ALPHA,
           "D1": {"mean": m1, "lower": lo1, "p": p1}, "D2": {"mean": m2, "lower": lo2, "p": p2,
                                                             "sessions_with_a_call": int(len(exc))},
           "ledger": base.perf(led), "ledger_2x_cost": base.perf(ledger(t, 2 * COST)),
           "holding_equal_weight": base.perf(t.groupby("session").label.mean()),
           "all_days_mean": float(t.label.mean())}
    recent = led[led.index >= "2025-01-01"]
    out["ledger_2025_on"] = {"sessions": int(len(recent)), "mean_day": float(recent.mean()) if len(recent) else None}
    sized = score.book(t, rules.scaled(t, score.base_weight(t.d_rv21)))
    out["sized_book"] = {**base.perf(sized.ret.reindex(sorted(t.session.unique())).fillna(0.0)),
                         "gross": float(sized.gross.mean())}
    per = []
    for s, g in t.groupby("symbol"):
        c = g[g.position > 0]
        if len(c) >= 30:
            per.append((s, int(len(c)), float(c.label.mean() - g.label.mean())))
    out["names_with_30_calls"] = len(per)
    out["names_calls_beat_own_average"] = int(sum(x[2] > 0 for x in per))
    out["per_name"] = sorted(per, key=lambda x: x[2])
    if split == "train":
        enough = names >= MIN_SCORED and len(calls) >= MIN_CALLS
        ok = bool(enough and m1 > 0 and lo1 > 0 and m2 > 0 and p2 < ALPHA)
        out["pass"] = ok
        out["verdict"] = "PASS" if ok else "FAIL" if enough else "NO VERDICT (too few names or calls): FAIL"
    else:
        out["consistent"] = bool(m1 > 0 and m2 > 0)
        out["verdict"] = "CONSISTENT" if out["consistent"] else "NOT CONSISTENT"
    return out


def record(symbols: list[str], tag: str, last: str, workers: int) -> tuple[pd.DataFrame, list[str]]:
    with ProcessPoolExecutor(max_workers=workers) as ex:
        got = list(ex.map(predict, [(s, tag, last) for s in symbols]))
    dropped = [s for s, t in got if t is None]
    parts = [t for _, t in got if t is not None]
    t = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["session", "symbol", "label", "pred", "position", "d_rv21"])
    return t.sort_values(["session", "symbol"], kind="stable").reset_index(drop=True), dropped


def bridge(workers: int) -> dict:
    """Descriptive: the 21 known names rebuilt from quarter-hour bars against their stored 1-minute records."""
    from sgt.intraday.store import ParquetBarStore

    store = ParquetBarStore("data/alpaca")
    for s in SEEN:
        p = CACHE / f"m15_{s}.parquet"
        if not p.exists():
            agg15(store.read_frame(s, START, END)).to_parquet(p)
    new, dropped = record(SEEN, "dfeat15", TRAIN[1], workers)
    old = base.load()
    old = old[(old.session >= TRAIN[0]) & (old.session <= TRAIN[1])]
    new = new[(new.session >= TRAIN[0]) & (new.session <= TRAIN[1])]
    m = old.merge(new, on=["session", "symbol"], suffixes=("_1m", "_15m"))
    a, b = m.position_1m > 0, m.position_15m > 0
    return {"names": int(new.symbol.nunique()), "dropped": dropped, "rows_in_common": int(len(m)),
            "forecast_correlation": float(np.corrcoef(m.pred_1m, m.pred_15m)[0, 1]),
            "long_calls_1m": int(a.sum()), "long_calls_15m": int(b.sum()), "in_common": int((a & b).sum()),
            "share_of_1m_calls_also_15m": float((a & b).sum() / max(1, a.sum())),
            "mean_long_call_1m": float(m.label_1m[a].mean()), "mean_long_call_15m": float(m.label_15m[b].mean()),
            "ledger_1m": base.perf(ledger(old)), "ledger_15m": base.perf(ledger(new))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    for flag in ("--universe", "--fetch", "--holdout", "--bridge"):
        g.add_argument(flag, action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    uni = OUT / "universe.json"
    if a.universe:
        if uni.is_file():
            print(f"{uni} exists: the universe is fixed once")
            return 2
        names = pick(rank())
        uni.write_text(json.dumps({"prereg": "docs/findings/prereg_fresh_names_2026-11-17.md", "names": names}, indent=1) + "\n")
        print(len(names), " ".join(names))
        return 0
    if a.bridge:
        res = bridge(a.workers)
        (OUT / "bridge.json").write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
        return 0
    names = json.loads(uni.read_text())["names"]
    if a.fetch:
        for s in ("SPY", "QQQ"):
            print(s, len(market_m15(s)), flush=True)

        def one(s: str) -> str:
            try:
                return f"{s} {fetch_m15(s).ts.dt.tz_convert(ET).dt.date.nunique()} sessions, daily {len(adjusted_daily(s))}"
            except (RuntimeError, ValueError, KeyError) as e:
                return f"{s} FAILED {type(e).__name__}: {str(e)[:80]}"
        for s in names:                                     # one at a time: the request spacing is the rate limit
            print(one(s), flush=True)
        return 0
    train = OUT / "result_train.json"
    if a.holdout:                                           # fail closed: held-out only after the training PASS
        if not train.is_file() or not json.loads(train.read_text()).get("pass"):
            print("the training run did not pass: the held-out slice stays closed")
            return 2
        if (OUT / "result_holdout.json").is_file():
            print("the held-out run was made once and is not repeated")
            return 2
    elif train.is_file():                                   # the training run is a single run
        print(f"{train} exists: the training run was made once and is not repeated")
        return 2
    window, split = (HOLD, "holdout") if a.holdout else (TRAIN, "train")
    t, dropped = record(names, "dfeat15", window[1], a.workers)
    res = {"prereg": "docs/findings/prereg_fresh_names_2026-11-17.md", **evaluate(t, window, split, dropped)}
    (OUT / f"result_{split}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps({k: v for k, v in res.items() if k != "per_name"}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
