"""Loss review: the facts of one fade trade before any lesson is drawn (owner process, 2026-11-06).

    python scripts/loss_review.py RAYA 2026-11-06            # on the runner (ops DB + Alpaca SIP bars)
    python scripts/loss_review.py RAYA 2026-11-06 --db path   # anywhere with a copy of the ops DB

Prints: the trade as recorded (live estimate and official), a minute-by-minute tape from 5 minutes before the alert to
15 minutes after the exit with halts (missing minutes) marked, the official tested entry/exit, the day-trader signals
at entry, and how trades with the same traits did historically. Step 1 of every review: check the chart someone
reasons from against this (RAYA: the "breakeven" dip was really +8-10% because the true entry was after a halt).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

from sgt.fast import signals
from sgt.fast.fade_rule import ET, STOP

DB = Path("/data/sgt/reports/ops/ops.db")
HIST = Path("reports/fade_news")             # dev/holdout event files with news flags and the champion's net


def trade(db: Path, symbol: str, session: str) -> dict:
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM fade_trades WHERE symbol=? AND session=? AND status!='filtered' ORDER BY id DESC",
                  (symbol, session)).fetchone()
    if r is None:
        raise SystemExit(f"no fade trade for {symbol} on {session}")
    return {k: r[k] for k in r.keys()}


def bars(symbol: str, session: str) -> pd.DataFrame:
    from fade_live import data_client

    # free SIP data ends 15 minutes before now: cap the window or the request is refused
    end = min(pd.Timestamp(f"{session}T23:59:00Z"), pd.Timestamp.now(tz="UTC") - pd.Timedelta(minutes=16))
    with data_client() as c:
        r = c.get("/v2/stocks/bars", params={"symbols": symbol, "timeframe": "1Min", "feed": "sip", "limit": 10000,
                                              "adjustment": "raw", "start": f"{session}T08:00:00Z",
                                              "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")})
        if r.status_code != 200:
            print(f"  SIP bars request refused: HTTP {r.status_code}")
        rows = (r.json().get("bars") or {}).get(symbol) or [] if r.status_code == 200 else []
    f = pd.DataFrame(rows)
    if f.empty:
        return f
    f["ts"] = pd.to_datetime(f.t, utc=True)
    f = f.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    m = f.ts.dt.tz_convert(ET).dt.hour * 60 + f.ts.dt.tz_convert(ET).dt.minute
    return f.assign(segment=["rth" if 570 <= x < 960 else "pre" if x < 570 else "post" for x in m])


def tape(f: pd.DataFrame, t: dict) -> list[str]:
    alert = pd.Timestamp(t["posted_at"])
    end = pd.Timestamp(t.get("est_exit_at") or alert + pd.Timedelta(minutes=60)) + pd.Timedelta(minutes=15)
    w = f[(f.ts >= alert - pd.Timedelta(minutes=5)) & (f.ts <= end)]
    entry = t.get("entry") or t.get("est_entry")
    out, prev = [], None
    for x in w.itertuples():
        if prev is not None and (x.ts - prev).total_seconds() > 60:
            gap = int((x.ts - prev).total_seconds() // 60) - 1
            out.append(f"  ---- {gap} minute(s) with no trades: HALT or no prints ----")
        pnl = f"  short P&L {(entry - x.close) / entry:+.1%}" if entry else ""
        out.append(f"  {x.ts.tz_convert(ET):%H:%M}  o {x.open:.3f} h {x.high:.3f} l {x.low:.3f} c {x.close:.3f}"
                   f"  v {int(x.volume):>9,}{pnl}")
        prev = x.ts
    return out


def history(t: dict) -> list[str]:
    """How fades with the same traits did (champion net, dev and held-out)."""
    if not (HIST / "dev_events.csv").exists():
        return ["  (history files not on this machine)"]
    out = []
    for name in ("dev", "holdout"):
        e = pd.read_csv(HIST / f"{name}_events.csv")
        own = e.news_own.astype(str).str.lower().isin(["true", "1"])
        news = e.alert_type.eq("news") | own
        is_news = t.get("alert_type") == "news" or bool(t.get("news_own"))
        grp = e.net[news] if is_news else e.net[~news]
        stopped = (e.net < -0.15).mean()
        out.append(f"  {name}: all {e.net.mean():+.2%} (n {len(e)}); same news group "
                   f"({'news' if is_news else 'no news'}) {grp.mean():+.2%} (n {len(grp)}); "
                   f"share of fades losing >15% {stopped:.0%}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol")
    ap.add_argument("session")
    ap.add_argument("--db", type=Path, default=DB)
    a = ap.parse_args()
    t = trade(a.db, a.symbol.upper(), a.session)
    f = bars(a.symbol.upper(), a.session)
    print(f"LOSS REVIEW {t['symbol']} {t['session']} ({t['alert_type']}) status {t['status']} reason {t['reason']}")
    print(f"  alert {pd.Timestamp(t['posted_at']).tz_convert(ET):%H:%M:%S} ET; live est entry {t.get('est_entry')} "
          f"at {t.get('est_entry_at')}; est exit {t.get('est_exit')} at {t.get('est_exit_at')}")
    print(f"  official: entry {t.get('entry')} cover {t.get('cover')} net {t.get('net')} (tested rule, SIP bars); "
          f"stop level = entry x {1 + STOP:.2f}")
    print(f"  context: news_own {t.get('news_own')} conf {t.get('conf')} IBKR shares {t.get('ib_avail')} "
          f"fee {t.get('ib_fee')} filing_dilution {t.get('filing_dilution')} StockTwits 60m {t.get('st_n60')}")
    if f.empty:
        print("  no SIP bars (older than the free 15-minute delay?)")
        return 0
    at = pd.Timestamp(t.get("entry_ts") or t.get("est_entry_at") or t["posted_at"])
    s = signals.snapshot(f, at)
    print("  signals at entry: " + ", ".join(f"{k} {v:.3g}" for k, v in s.items() if v is not None))
    print("TAPE (ET, 1-minute SIP)")
    print("\n".join(tape(f, t)))
    print("HISTORY (same traits)")
    print("\n".join(history(t)))
    print("NEXT: turn each proposed lesson into an exact rule, pre-register it, test it on 3,113 past fades "
          "(docs/runbooks/loss_review.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
