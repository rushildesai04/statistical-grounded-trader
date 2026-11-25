"""Does a price survive from the source's text to our stored value? (owner 2026-11-25: "Verify source-price
preservation. Float storage does not inherently preserve the original decimal exactly.")

True in general: a 64-bit float cannot hold most decimals exactly. What ``sgt.fast.exact_price`` relies on is
narrower and checkable: a decimal with at most 15 significant digits, parsed into a double and read back by its
SHORTEST round-trip form, gives the same decimal. This script checks that on real data instead of assuming it:

  sent    the number exactly as the source wrote it (the response text parsed with decimals, no float in between)
  parsed  the same response parsed the way the pipeline does (JSON numbers -> doubles), read back shortest-form
  stored  the value in our bar store for the same minute, read back shortest-form

For every open, high, low and close it counts: sent == parsed, sent == stored, and how many values the SOURCE itself
wrote with more than 6 decimal places (those are refused by ``exact_price.price``, never rounded). It also reports
values the source wrote that look like float artefacts (15 or more significant digits).

It cannot check what happened BEFORE the source wrote the number (the vendor's own handling of the exchange's
price): that is outside what we can see.

READ-ONLY: one request per symbol-day to the market data API, the bar store is only read. Usage:
    python scripts/price_source_check.py GLXG:2026-11-24 RAIN:2026-11-17
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path("/app")
sys.path.insert(0, str(HERE / "src"))
BASE, MAX_DP, FIELDS = "https://data.alpaca.markets", 6, (("o", "open"), ("h", "high"), ("l", "low"), ("c", "close"))


def places(d: Decimal) -> int:
    return max(0, -d.normalize().as_tuple().exponent)


def check(client, store, symbol: str, session: str) -> dict:
    end = min(datetime.fromisoformat(f"{session}T23:59:00+00:00"), datetime.now(timezone.utc) - timedelta(minutes=17))
    time.sleep(0.4)
    r = client.get(f"/v2/stocks/{symbol}/bars", params={"timeframe": "1Min", "feed": "sip", "adjustment": "raw",
                                                       "limit": 10000, "start": f"{session}T09:00:00+00:00",
                                                       "end": end.isoformat()})
    if r.status_code != 200:
        return {"symbol": symbol, "session": session, "error": f"status {r.status_code}"}
    exact = json.loads(r.text, parse_float=Decimal, parse_int=Decimal).get("bars") or []
    floats = json.loads(r.text).get("bars") or []
    out = {"symbol": symbol, "session": session, "bars": len(exact), "values": 0, "parsed_differs": 0, "stored_compared": 0,
           "stored_differs": 0, "over_6_places": 0, "long_digits": 0, "places": {}, "examples": []}
    kept = {}
    if store is not None:
        d = date.fromisoformat(session)
        f = store.read_frame(symbol, d, d)
        if len(f):
            import pandas as pd

            f = f.assign(ts=pd.to_datetime(f.ts, utc=True))
            kept = {t.isoformat(): row for t, row in zip(f.ts, f.itertuples(index=False))}
    for a, b in zip(exact, floats):
        for k, name in FIELDS:
            sent = a[k]
            out["values"] += 1
            out["places"][places(sent)] = out["places"].get(places(sent), 0) + 1
            if places(sent) > MAX_DP:
                out["over_6_places"] += 1
            if len(sent.normalize().as_tuple().digits) >= 15:
                out["long_digits"] += 1
            if Decimal(repr(float(b[k]))) != sent:
                out["parsed_differs"] += 1
                out["examples"].append(f"{a['t']} {name}: sent {sent} parsed {float(b[k])!r}")
            row = kept.get(datetime.fromisoformat(str(a["t"]).replace("Z", "+00:00")).isoformat())
            if row is not None:
                out["stored_compared"] += 1
                if Decimal(repr(float(getattr(row, name)))) != sent:
                    out["stored_differs"] += 1
                    out["examples"].append(f"{a['t']} {name}: sent {sent} stored {float(getattr(row, name))!r}")
    out["examples"] = out["examples"][:5]
    return out


def main(argv: list[str]) -> int:
    import httpx

    from sgt.core.secrets import get_secret

    os.chdir(os.environ.get("SGT_HOME") or ".")
    store = None
    try:
        from sgt.intraday.store import ParquetBarStore

        store = ParquetBarStore(Path("data/fast"))
    except Exception as exc:  # noqa: BLE001 - without the store the source-to-parse half is still checked
        print("bar store not readable:", type(exc).__name__)
    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id") or "", "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key") or ""}
    bad = 0
    with httpx.Client(base_url=BASE, headers=h, timeout=10) as client:
        for arg in argv[:20]:
            symbol, _, session = arg.partition(":")
            res = check(client, store, symbol.upper(), session)
            bad += int(bool(res.get("parsed_differs") or res.get("stored_differs") or res.get("error")))
            print(json.dumps(res, default=str))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["GLXG:2026-11-24", "RAIN:2026-11-17", "JZ:2026-11-24", "BENF:2026-11-24",
                                           "ALGS:2026-11-25", "BNAI:2026-11-25"]))
