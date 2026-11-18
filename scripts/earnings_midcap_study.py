"""The earnings follow-through on mid-size stocks (docs/findings/prereg_earnings_midcap_2026-11-18.md).

    .venv/bin/python scripts/earnings_midcap_study.py fetch    # SEC filing times + adjusted daily bars (no returns)
    .venv/bin/python scripts/earnings_midcap_study.py dev      # development 2017-2023, run ONCE
    .venv/bin/python scripts/earnings_midcap_study.py score    # held-out 2024-2026, once, only after a dev PASS

Report only: nothing here changes a book. Events and the trade are the earlier large-cap study's code, unchanged
(``sgt.catalysts.earnings``); only the universe and the cost differ."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.catalysts.earnings import CONFIGS, Edgar, config_trades, date_lower_bound, event_prices, events
from sgt.config import get_settings
from sgt.core.secrets import get_secret
from sgt.intraday.sources import _alpaca_headers

OUT = Path("reports/earnings_midcap")
CACHE = Path("data/earnings_midcap")
DAILY = Path("data/daily")
CONFIG = "close_cont_m5%_x5"                       # the one configuration the large-cap study selected
COST = 0.0030
DV_LO, DV_HI, PX_MIN, MIN_2016 = 10e6, 100e6, 5.0, 200
DEV = (pd.Timestamp("2017-01-03"), pd.Timestamp("2023-12-29"))
HOLD = (pd.Timestamp("2024-01-02"), pd.Timestamp("2026-10-29"))
BARS_FROM, BARS_TO = "2016-11-01", "2026-10-30"
MIN_DEV, MIN_HOLD = 300, 100
SEED, N_BOOT = 20261118, 10_000
THROTTLE_S = 0.8                                   # daily-bar requests: far below the shared data budget
MAX_BARS = 4_000
LARGE = frozenset((
    "AAPL MSFT AMZN GOOGL META NVDA TSLA AVGO JPM V MA LLY UNH XOM WMT ORCL AMD NFLX COST HD PG JNJ ABBV MRK PEP KO CVX "
    "BAC WFC C GS MS AXP BLK SCHW ADBE CRM CSCO INTC IBM QCOM TXN AMAT MU INTU NOW AMGN GILD BMY PFE TMO DHR ABT MDT ISRG "
    "CVS CI ELV MCD SBUX NKE LOW TGT BKNG DIS CMCSA T VZ TMUS CAT DE HON GE MMM BA LMT RTX UPS FDX UNP NEE DUK SO LIN COP "
    "SPG AMT PLD PYPL MO PM MDLZ CL KHC GM F USB ACN UBER").split())
BUCKETS = (("5-10%", 0.05, 0.10), ("10-20%", 0.10, 0.20), ("above 20%", 0.20, np.inf))


def screen(stats: pd.DataFrame) -> pd.DataFrame:
    """The universe rule on a table of 2016 activity (symbol, n16, dv16, px16): mid-size, liquid, not a large cap."""
    m = stats[(stats.n16 >= MIN_2016) & (stats.dv16 >= DV_LO) & (stats.dv16 < DV_HI) & (stats.px16 >= PX_MIN)
              & ~stats.symbol.isin(LARGE)]
    return m.sort_values("symbol").reset_index(drop=True)


def activity_2016(root: Path = DAILY) -> pd.DataFrame:
    rows = []
    for p in sorted(root.glob("*.parquet")):
        try:
            x = pd.read_parquet(p, columns=["date", "close", "volume"])
        except Exception:  # noqa: BLE001 - an unreadable file is simply not in the universe
            continue
        y = x[(x.date >= "2016-01-01") & (x.date <= "2016-12-31")]
        if len(y):
            rows.append({"symbol": p.stem, "n16": int(len(y)), "dv16": float((y.close * y.volume).median()),
                         "px16": float(y.close.median())})
    return pd.DataFrame(rows)


def parse_daily(rows) -> pd.DataFrame:
    """Whole, numeric daily bars (external bytes), indexed by date, at most MAX_BARS."""
    ok = [x for x in (rows if isinstance(rows, list) else [])[:MAX_BARS]
          if isinstance(x, dict) and isinstance(x.get("t"), str)
          and all(isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) and x.get(k) > 0 for k in "ohlc")]
    f = pd.DataFrame({"date": pd.to_datetime([x["t"][:10] for x in ok], errors="coerce"),
                      **{n: [float(x[k]) for x in ok] for k, n in zip("ohlc", ("open", "high", "low", "close"))}})
    return f.dropna(subset=["date"]).drop_duplicates("date").set_index("date").sort_index()


def daily(sym: str, client: httpx.Client | None = None) -> pd.DataFrame | None:
    """Adjusted daily bars of one symbol (cached). None when they could not be fetched; with no client, cache only."""
    path = CACHE / f"daily_{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    if client is None:
        return None
    url, rows, token = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars", [], None
    for _ in range(5):
        params = {"symbols": sym, "timeframe": "1Day", "start": BARS_FROM, "end": BARS_TO, "adjustment": "all", "feed": "sip",
                  "limit": 10000}
        if token:
            params["page_token"] = token
        try:
            r = client.get(url, params=params, headers=_alpaca_headers())
            j = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        time.sleep(THROTTLE_S)
        if not isinstance(j, dict):
            return None
        got = (j.get("bars") or {}).get(sym) if isinstance(j.get("bars"), dict) else None
        rows += got if isinstance(got, list) else []
        token = j.get("next_page_token")
        if not isinstance(token, str) or not token:
            break
    f = parse_daily(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    f.to_parquet(path)
    return f


def cmd_fetch() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    uni = screen(activity_2016())
    uni.to_csv(OUT / "universe.csv", index=False)
    e = Edgar(get_secret("sec_contact"))
    no_cik = bad = 0
    try:
        ciks = e.ciks()
        with httpx.Client(timeout=30) as client:
            daily("SPY", client)
            for k, sym in enumerate(uni.symbol, 1):
                if sym not in ciks:
                    no_cik += 1
                    continue
                path = CACHE / f"raw_{sym}.parquet"
                try:
                    if not path.exists():
                        e.earnings(ciks[sym]).to_parquet(path)
                    if daily(sym, client) is None:
                        bad += 1
                except Exception as exc:  # noqa: BLE001 - one name failing never stops the fetch; counted, asked again
                    bad += 1
                    print(f"{sym}: {type(exc).__name__}", flush=True)
                if k % 100 == 0:
                    print(f"{k}/{len(uni)} names", flush=True)
    finally:
        e.close()
    print(f"done: universe {len(uni)}, no SEC mapping {no_cik}, failed {bad} (run again to retry)", flush=True)
    return 0 if not bad else 1


def all_prices() -> tuple[pd.DataFrame, dict]:
    """Event prices of every universe name that has filings and bars (cache only: no network here)."""
    uni = pd.read_csv(OUT / "universe.csv", converters={"symbol": str})
    spy = daily("SPY")
    if spy is None:
        raise SystemExit("no SPY bars cached: run fetch first")
    out, used = [], 0
    for r in uni.itertuples():
        raw_p = CACHE / f"raw_{r.symbol}.parquet"
        px = daily(r.symbol)
        if not raw_p.exists() or px is None or px.empty:
            continue
        raw = pd.read_parquet(raw_p)
        if raw.empty:
            continue
        p = event_prices(events(raw, spy.index), px, spy)
        if len(p):
            used += 1
            out.append(p.assign(sym=r.symbol, dv16=r.dv16))
    return (pd.concat(out, ignore_index=True) if out else pd.DataFrame()), {"universe": int(len(uni)), "names_with_events": used}


def window(p: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    """Events whose reaction session AND exit (R+5) lie inside [lo, hi]."""
    return p[(p.R >= lo) & (p.d5 <= hi)].reset_index(drop=True)


def stats(t: pd.DataFrame, rng, col: str = "net") -> dict:
    if t.empty:
        return {"n": 0, "mean": None, "lower95": None, "win": None}
    return {"n": int(len(t)), "mean": float(t[col].mean()), "lower95": date_lower_bound(t, 0.05, rng, N_BOOT, col),
            "win": float((t[col] > 0).mean())}


def report(p: pd.DataFrame) -> dict:
    """Every number of one period: the decided trade and the reported views."""
    rng = np.random.default_rng(SEED)
    cfg = CONFIGS[CONFIG]
    t = config_trades(p, cfg, COST)
    t = t.assign(sym=p.sym.to_numpy()[: 0] if t.empty else p.loc[(p.c0 / p.c_1 - 1).abs() >= cfg["m"], "sym"].to_numpy(),
                 dv16=np.array([]) if t.empty else p.loc[(p.c0 / p.c_1 - 1).abs() >= cfg["m"], "dv16"].to_numpy())
    main = stats(t, rng)
    lb = main["lower95"]
    res = {"events_all": int(len(p)), "names": int(p.sym.nunique()) if len(p) else 0, "main": main,
           "mean_2x_cost": float(config_trades(p, cfg, 2 * COST).net.mean()) if len(t) else None,
           "unhedged": float(t.raw.mean()) if len(t) else None,
           "long_leg": stats(t[t.side > 0], np.random.default_rng(SEED)), "short_leg": stats(t[t.side < 0], np.random.default_rng(SEED)),
           "by_year": {int(y): {"n": int(len(g)), "mean": float(g.net.mean())} for y, g in t.groupby(pd.to_datetime(t.R).dt.year)} if len(t) else {},
           "by_reaction": {name: stats(t[(t.x.abs() >= a) & (t.x.abs() < b)], np.random.default_rng(SEED)) for name, a, b in BUCKETS} if len(t) else {},
           "events_per_month": float(len(t) / max(1, pd.to_datetime(t.R).dt.to_period("M").nunique())) if len(t) else 0.0,
           "other_exits": {f"x{x}": stats(config_trades(p, {**cfg, "exit": x}, COST), np.random.default_rng(SEED)) for x in (1, 3)},
           "lower_ok": bool(lb is not None and lb == lb and lb > 0)}
    if len(t) >= 30:
        q = pd.qcut(t.dv16, 3, labels=["least traded third", "middle third", "most traded third"], duplicates="drop")
        res["by_liquidity"] = {str(k): stats(g, np.random.default_rng(SEED)) for k, g in t.groupby(q, observed=True)}
    return res


def dev_pass(res: dict) -> bool:
    return bool(res["main"]["n"] >= MIN_DEV and res["lower_ok"])


def hold_pass(res: dict) -> dict:
    years = [v["mean"] > 0 for y, v in res["by_year"].items() if y in (2024, 2025, 2026)]
    checks = {"lower bound above 0": res["lower_ok"], "positive at twice the cost": bool((res["mean_2x_cost"] or -1) > 0),
              "2 of 3 years positive": sum(years) >= 2, "at least 100 events": res["main"]["n"] >= MIN_HOLD}
    return {"checks": checks, "pass": all(checks.values())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("fetch", "dev", "score"))
    a = ap.parse_args(argv)
    if a.cmd == "fetch":
        return cmd_fetch()
    dev_path, ho_path = OUT / "result_dev.json", OUT / "result_holdout.json"
    if a.cmd == "dev":
        if dev_path.is_file():
            print(f"{dev_path} exists: the development run was made once and is not repeated")
            return 2
        p, cov = all_prices()
        res = {"prereg": "docs/findings/prereg_earnings_midcap_2026-11-18.md", "config": CONFIG, "cost": COST, **cov,
               **report(window(p, *DEV))}
        res["pass"] = dev_pass(res)
        dev_path.write_text(json.dumps(res, indent=1, default=str) + "\n")
        print(json.dumps(res, indent=1, default=str))
        return 0
    if ho_path.is_file():
        print(f"{ho_path} exists: the held-out run was made once and is not repeated")
        return 2
    if not dev_path.is_file() or not json.loads(dev_path.read_text()).get("pass"):
        print("the held-out years are opened only after a development PASS")
        return 2
    p, cov = all_prices()
    res = {"prereg": "docs/findings/prereg_earnings_midcap_2026-11-18.md", "config": CONFIG, "cost": COST, **cov,
           **report(window(p, *HOLD))}
    res.update(hold_pass(res))
    ho_path.write_text(json.dumps(res, indent=1, default=str) + "\n")
    print(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
