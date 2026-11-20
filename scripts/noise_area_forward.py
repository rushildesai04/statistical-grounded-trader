"""Forward record of the frozen intraday trend rule on SPY and QQQ (docs/findings/prereg_noise_area_forward_2026-11-19.md).

    python scripts/noise_area_forward.py record     # nightly: score the sessions not yet in the ledger, then decide once

Owner 2026-11-19: "Record forward". No order, no text: a ledger and one line in the nightly summary. The rule is
``scripts/noise_area_study.py`` as it was scored; nothing here changes a parameter of it."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import noise_area_study as nas  # noqa: E402

from sgt.ops.forward_record import noise_area_line  # noqa: E402

PLAN = "docs/findings/prereg_noise_area_forward_2026-11-19.md"
OUT = Path("reports/noise_area_forward")
SYMBOLS = ("SPY", "QQQ")
FIRST = "2026-11-20"                    # the first session after the owner's word
DECIDE_AT = 250
ALPHA = 0.05 / len(SYMBOLS)
WARMUP = timedelta(days=45)             # calendar days fetched before the first unscored session (14 sessions needed)
SETTLED = timedelta(minutes=20)         # the official tape is free once about 16 minutes old
MAX_BARS = 60_000                       # one instrument's fetch: bounded
COLS = ["symbol", "session", "ret", "gross", "trades", "wins", "recorded_at"]
ET = ZoneInfo("America/New_York")


def frame(bars) -> pd.DataFrame:
    """Vendor bars -> the study's frame. External data: whole, positive, finite bars only, bounded in number."""
    rows = []
    for b in list(bars)[:MAX_BARS]:
        try:
            o, h, lo, c, v = float(b.open), float(b.high), float(b.low), float(b.close), float(b.volume)
        except (TypeError, ValueError):
            continue
        if not all(x == x and x > 0 for x in (o, h, lo, c)) or not v >= 0:
            continue
        rows.append({"session_date": str(b.session_date), "minute_of_day": int(b.minute_of_day), "open": o, "high": h,
                     "low": lo, "close": c, "volume": v, "vwap": float(b.vwap) if b.vwap else float("nan"),
                     "segment": getattr(b.segment, "value", str(b.segment))})
    return pd.DataFrame(rows, columns=["session_date", "minute_of_day", "open", "high", "low", "close", "volume", "vwap", "segment"])


def last_complete(now: datetime) -> str:
    """The latest session whose official bars are complete at ``now``: today once the close is SETTLED old, else the
    previous calendar day (a weekend or holiday simply has no bars and adds no row)."""
    et = now.astimezone(ET)
    done = et >= datetime.combine(et.date(), time(16, 0), ET) + SETTLED
    return (et.date() if done else et.date() - timedelta(days=1)).isoformat()


def ledger(out: Path = OUT) -> pd.DataFrame:
    p = out / "ledger.csv"
    return pd.read_csv(p, dtype={"symbol": str, "session": str}) if p.is_file() else pd.DataFrame(columns=COLS)


def new_rows(symbol: str, f: pd.DataFrame, known: set[str], until: str, now: datetime) -> list[dict]:
    """One row per session of ``f`` from FIRST to ``until`` that is not in the ledger, scored by the study's own
    simulator on that single session (the sessions before it only feed the boundaries)."""
    if f.empty:
        return []
    s = nas.sessions_from_bars(f)
    rows = []
    for day in s["days"]:
        if not FIRST <= day <= until or day in known:
            continue
        d = nas.simulate(s, rule="B", fill="next", cost=nas.COSTS["careful"], sized="full", start=day, end=day)
        if len(d) != 1:
            continue                    # too short a session or no open: not a scored session
        r = d.iloc[0]
        rows.append({"symbol": symbol, "session": day, "ret": float(r.ret), "gross": float(r.gross), "trades": int(r.trades),
                     "wins": int(r.wins), "recorded_at": now.astimezone(timezone.utc).isoformat()})
    return rows


def record(now: datetime, fetch, out: Path = OUT) -> dict:
    """``fetch(symbol, start, end)`` -> vendor bars. Appends the unscored sessions; a session is never rewritten."""
    out.mkdir(parents=True, exist_ok=True)
    led = ledger(out)
    until = last_complete(now)
    added = []
    for sym in SYMBOLS:
        if until < FIRST:
            break                       # no forward session is complete yet
        known = set(led[led.symbol == sym].session)
        if until in known:
            continue                    # nothing new for this instrument: no request
        start = date.fromisoformat(max(known) if known else FIRST) - WARMUP
        end = datetime.combine(date.fromisoformat(until) + timedelta(days=1), time(0), ET)
        f = frame(fetch(sym, datetime.combine(start, time(0), ET).astimezone(timezone.utc), end.astimezone(timezone.utc)))
        added += new_rows(sym, f, known, until, now)
    if added:
        led = pd.concat([led, pd.DataFrame(added, columns=COLS)], ignore_index=True)
        tmp = out / "ledger.tmp"
        led.to_csv(tmp, index=False)
        tmp.replace(out / "ledger.csv")
    return {"added": len(added), "until": until, "sessions": {s: int((led.symbol == s).sum()) for s in SYMBOLS},
            "decision": decide(led, out, now)}


def decide(led: pd.DataFrame, out: Path = OUT, now: datetime | None = None) -> dict | None:
    """Once, on the first night both instruments have DECIDE_AT sessions; the stored result is returned afterwards."""
    p = out / "result.json"
    if p.is_file():
        return json.loads(p.read_text())
    if any(int((led.symbol == s).sum()) < DECIDE_AT for s in SYMBOLS):
        return None
    res: dict = {"plan": PLAN, "decided_at": (now or datetime.now(timezone.utc)).isoformat(), "alpha": ALPHA, "by": {}}
    for s in SYMBOLS:
        x = led[led.symbol == s].sort_values("session").ret.to_numpy(float)[:DECIDE_AT]
        m, lb = float(x.mean()), nas.lower_bound(x, alpha=ALPHA)
        res["by"][s] = {"sessions": int(len(x)), "mean_daily": m, "lower_bound": lb, "sum": float(x.sum()),
                        "verdict": "PASS" if m > 0 and lb is not None and lb > 0 else "CLOSED"}
    p.write_text(json.dumps(res, indent=1) + "\n")
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("record",))
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    from sgt.intraday.sources import AlpacaHistoricalBars

    src = AlpacaHistoricalBars(feed="sip")
    try:
        res = record(datetime.now(timezone.utc), src.fetch, a.out)
    finally:
        src.close()
    print(json.dumps(res, default=str))
    print(noise_area_line(a.out / "ledger.csv", DECIDE_AT) or "trend rule (record only): no session recorded yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
