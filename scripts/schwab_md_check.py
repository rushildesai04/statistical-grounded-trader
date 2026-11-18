"""Go / no-go for Schwab as the live candle source (owner 2026-11-18, decision B). Read-only.

    .venv/bin/python scripts/schwab_md_check.py CUE SVRE CLRO              # today so far; run in market hours
    .venv/bin/python scripts/schwab_md_check.py CLRO --date 2026-11-18     # a past session (no real-time verdict)

Needs a Schwab login on this machine (``scripts/schwab_auth.py``) for an app that has "Market Data Production".
Two questions, each answered per symbol and then overall:
  FULL TAPE  do Schwab's 1-minute candles match the official tape (which is free once 15 minutes old)?
  REAL TIME  is the minute that just closed already there?
Prints numbers only: never a token, key or account detail. Exit 0 = both yes, 1 = a no, 2 = no usable login.
Run it alone: it takes an access token through the one token manager, like any other Schwab process."""
from __future__ import annotations

import argparse
import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from sgt.brokers.schwab_auth import SCHWAB_BASE_URL, SchwabTokenManager
from sgt.config import get_settings
from sgt.core.interfaces import BrokerError
from sgt.core.secrets import MissingSecretError
from sgt.intraday import schwab_md
from sgt.intraday.sources import _alpaca_headers

ET = ZoneInfo("America/New_York")
TAPE_LAG = timedelta(minutes=17)          # official bars younger than this are not free
MAX_SYMBOLS = 10


def tape_bars(client: httpx.Client, symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    """Official 1-minute bars (all exchanges) in [start, end]; empty on any failure. Bounded pages."""
    url, rows, token = get_settings().alpaca_data_url.rstrip("/") + f"/v2/stocks/{symbol}/bars", [], None
    for _ in range(3):
        params = {"timeframe": "1Min", "feed": "sip", "limit": 10000, "adjustment": "raw",
                  "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if token:
            params["page_token"] = token
        try:
            r = client.get(url, params=params, headers=_alpaca_headers())
            j = r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return pd.DataFrame(columns=schwab_md.COLUMNS)
        if not isinstance(j, dict) or not isinstance(j.get("bars") or [], list):
            return pd.DataFrame(columns=schwab_md.COLUMNS)
        rows += [x for x in (j.get("bars") or []) if isinstance(x, dict)]
        token = j.get("next_page_token")
        time.sleep(1.0)                    # the runner shares this data account
        if not token:
            break
    ok = [x for x in rows if isinstance(x.get("t"), str) and all(schwab_md._num(x.get(k)) for k in "ohlcv")]
    f = pd.DataFrame({"ts": pd.to_datetime([x["t"] for x in ok], utc=True, errors="coerce"),
                      **{n: [float(x[k]) for x in ok] for k, n in zip("ohlcv", schwab_md.COLUMNS[1:])}})
    return f.dropna(subset=["ts"]).sort_values("ts").reset_index(drop=True)


async def run(symbols: list[str], day: date, now: datetime) -> int:
    start = datetime.combine(day, datetime.min.time(), ET).replace(hour=9, minute=30).astimezone(timezone.utc)
    close = start + timedelta(hours=6, minutes=30)
    live = day == now.astimezone(ET).date() and start <= now < close
    end = min(now, close)
    try:
        token = await SchwabTokenManager.shared().access_token()
    except (BrokerError, MissingSecretError) as exc:
        print(f"no usable Schwab login ({type(exc).__name__}): run scripts/schwab_auth.py first")
        return 2
    ok = True
    async with httpx.AsyncClient(base_url=SCHWAB_BASE_URL, timeout=15) as sc:
        with httpx.Client(timeout=30) as ac:
            for sym in symbols:
                got = await schwab_md.price_history(sc, token, sym, start, end + timedelta(minutes=1))
                if got is None:
                    print(f"{sym}: Schwab request failed (see the log line above: HTTP status only)")
                    ok = False
                    continue
                got = got[(got.ts >= start) & (got.ts < close)]
                tape = tape_bars(ac, sym, start, min(end, now - TAPE_LAG))
                a = schwab_md.agreement(got, tape)
                line = (f"{sym}: tape minutes {a['tape_minutes']}, in both {a['both']}"
                        + (f", high within a cent {a['high_match']:.0%}, low within a cent {a['low_match']:.0%}, "
                           f"Schwab volume / tape volume {a['volume_share']:.0%}" if a["both"] else "")
                        + f" -> FULL TAPE {'YES' if a['full_tape'] else 'NO'}")
                if live:
                    fr = schwab_md.freshness(got, datetime.now(timezone.utc))
                    line += (f"; newest candle ended {fr['age_s']:.0f} s ago -> REAL TIME {'YES' if fr['real_time'] else 'NO'}"
                             if fr["age_s"] is not None else "; no candle -> REAL TIME NO")
                    ok &= fr["real_time"]
                ok &= a["full_tape"]
                print(line)
    if not live:
        print("not inside today's session: REAL TIME was not judged (run again between 09:30 and 16:00 ET)")
    print("VERDICT: " + ("Schwab candles are usable" if ok and live else "full tape only" if ok else "NOT usable as checked"))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="+")
    ap.add_argument("--date", type=date.fromisoformat, default=None)
    a = ap.parse_args(argv)
    syms = [s.upper() for s in a.symbols][:MAX_SYMBOLS]
    bad = [s for s in syms if not schwab_md.SYMBOL.match(s)]
    if bad:
        print("not plain stock symbols: " + ", ".join(bad))
        return 2
    now = datetime.now(timezone.utc)
    return asyncio.run(run(syms, a.date or now.astimezone(ET).date(), now))


if __name__ == "__main__":
    raise SystemExit(main())
