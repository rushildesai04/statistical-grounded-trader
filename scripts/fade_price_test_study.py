"""Does the fade's training result sit in stocks under the short-sale price test? (prereg_fade_price_test_2026-11-21)

    python scripts/fade_price_test_study.py          # ONCE; refuses to run again once its result exists

A diagnosis: it adopts nothing. Training file only (`reports/fade_news/dev_events.csv`, no-news fades); the held-out
file is never opened. Daily bars are fetched throttled and cached under the output folder (data, not results).
External bytes: daily bars are parsed strictly; a symbol whose bars cannot be read leaves its events "unknown".
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.fast import fade
from sgt.intraday.store import ParquetBarStore

SRC = Path("reports/fade_news/dev_events.csv")
OUT = Path("reports/fade_price_test")
HOLDOUT_START = "2026-07-01"
SEED, N_BOOT, MIN_N = 20261121, 10_000, 100
THROTTLE, TIMEOUT = 0.32, 15.0
MINUTE = pd.Timedelta(minutes=1)


def price_test(rth: pd.DataFrame, at: pd.Timestamp, prior_close: float | None, prior_low: float | None,
               close_before: float | None) -> bool | None:
    """The flag of the execution model (`exec_model_2026-11-21.md`, amendments 1-2): regular-hours minutes completed
    before ``at``; None when the prior close is unknown."""
    if not prior_close or prior_close <= 0:
        return None
    before = rth[rth.ts + MINUTE <= at]
    today = bool(len(before) and float(before.low.min()) <= 0.9 * prior_close)
    carried = bool(prior_low and close_before and prior_low <= 0.9 * close_before)
    return today or carried


def daily_bars(client: httpx.Client, symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    """Unadjusted official daily bars (date, low, close), from the cache or one throttled request."""
    f = cache / f"{symbol}.json"
    if f.exists():
        rows = json.loads(f.read_text())
    else:
        time.sleep(THROTTLE)
        try:
            r = client.get(f"/v2/stocks/{symbol}/bars", params={"timeframe": "1Day", "feed": "sip", "adjustment": "raw",
                                                               "limit": 10_000, "start": start.isoformat(), "end": end.isoformat()})
            got = r.json().get("bars") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            got = None
        if not isinstance(got, list):
            return pd.DataFrame(columns=["d", "low", "close"])            # unknown: not cached, never guessed
        rows = [{"d": str(x.get("t"))[:10], "low": x.get("l"), "close": x.get("c")} for x in got
                if isinstance(x, dict) and all(isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool)
                                               for k in ("l", "c"))]
        f.write_text(json.dumps(rows))
    return pd.DataFrame(rows, columns=["d", "low", "close"]).sort_values("d").reset_index(drop=True)


def flags(ev: pd.DataFrame, store: ParquetBarStore, client: httpx.Client, cache: Path) -> pd.Series:
    out = pd.Series(index=ev.index, dtype=object)
    for sym, g in ev.groupby("symbol"):
        lo = date.fromisoformat(g.session.min()) - timedelta(days=20)
        d = daily_bars(client, sym, lo, date.fromisoformat(g.session.max()), cache)
        for i, r in g.iterrows():
            prev = d[d.d < r.session]
            if not len(prev):
                out[i] = None
                continue
            pc, pl = float(prev.close.iloc[-1]), float(prev.low.iloc[-1])
            ppc = float(prev.close.iloc[-2]) if len(prev) > 1 else None
            day = date.fromisoformat(r.session)
            b = store.read_frame(sym, day, day)
            rth = b[b.segment == "rth"].assign(ts=lambda x: pd.to_datetime(x.ts, utc=True)) if len(b) else \
                pd.DataFrame({"ts": pd.Series(dtype="datetime64[ns, UTC]"), "low": pd.Series(dtype=float)})
            at = pd.Timestamp(fade.entry_start(pd.Timestamp(r.posted_at).to_pydatetime()))
            out[i] = price_test(rth, at, pc, pl, ppc)
    return out


def boot(sessions: np.ndarray, net: np.ndarray, flag: np.ndarray, rng: np.random.Generator) -> dict:
    """Session-clustered bootstrap of the two group means and their difference (flagged - unflagged)."""
    u, idx = np.unique(sessions, return_inverse=True)
    s = [np.bincount(idx, np.where(flag == k, net, 0.0), len(u)) for k in (True, False)]
    n = [np.bincount(idx, (flag == k).astype(float), len(u)) for k in (True, False)]
    pick = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        m = [s[k][pick].sum(1) / n[k][pick].sum(1) for k in (0, 1)]
    q = lambda x: [float(v) for v in np.nanquantile(x, [0.025, 0.975])]  # noqa: E731
    return {"flagged_ci": q(m[0]), "unflagged_ci": q(m[1]), "diff_ci": q(m[0] - m[1])}


def outcome(n_f: int, n_u: int, b: dict) -> str:
    if n_f < MIN_N or n_u < MIN_N:
        return "TOO FEW"
    lo, hi = b["diff_ci"]
    if lo > 0:
        return "ALSO OUTSIDE" if b["unflagged_ci"][0] > 0 else "SITS IN PRICE-TEST STOCKS"
    return "REVERSED" if hi < 0 else "NOT SHOWN"


def main() -> int:
    if (OUT / "result.json").exists():
        print("already run: reports/fade_price_test/result.json exists (single run, prereg_fade_price_test_2026-11-21)")
        return 1
    from sgt.core.secrets import get_secret

    ev = pd.read_csv(SRC)
    ev = ev[ev.news_own == 0].reset_index(drop=True)
    assert (ev.session < HOLDOUT_START).all(), "the training file holds held-out sessions"
    cache = OUT / "daily"
    cache.mkdir(parents=True, exist_ok=True)
    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    with httpx.Client(base_url="https://data.alpaca.markets", headers=h, timeout=TIMEOUT) as client:
        ev["flag"] = flags(ev, ParquetBarStore("data/alpaca"), client, cache)
    known = ev[ev.flag.notna()]
    f, u = known[known.flag == True], known[known.flag == False]  # noqa: E712 - object column of True / False / None
    b = boot(known.session.to_numpy(), known.net.to_numpy(float), known.flag.to_numpy(bool), np.random.default_rng(SEED))
    res = {"events": int(len(ev)), "unknown": int(ev.flag.isna().sum()), "sessions": int(known.session.nunique()),
           "flagged": {"n": int(len(f)), "mean": float(f.net.mean()) if len(f) else None, "win": float((f.net > 0).mean()) if len(f) else None},
           "unflagged": {"n": int(len(u)), "mean": float(u.net.mean()) if len(u) else None, "win": float((u.net > 0).mean()) if len(u) else None},
           "diff": float(f.net.mean() - u.net.mean()) if len(f) and len(u) else None, **b,
           "all_mean": float(ev.net.mean()), "seed": SEED, "n_boot": N_BOOT}
    res["outcome"] = outcome(len(f), len(u), b)
    ev[["symbol", "session", "posted_at", "net", "flag"]].to_csv(OUT / "events.csv", index=False)
    (OUT / "result.json").write_text(json.dumps(res, indent=2))
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
