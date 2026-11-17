"""The multi-day drift after an alert on stocks IBKR actually lends, at the recorded fee: forward only
(docs/findings/prereg_after_day_borrow_forward_2026-11-17.md).

    python scripts/after_day_borrow_study.py --count     # how many alert days are eligible so far (no outcome)
    python scripts/after_day_borrow_study.py             # the decision, ONCE, refused before 300 borrowable alert days

Runs where the borrow snapshots are (the runner's volume). Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
OUT = Path("reports/after_day_borrow")
FORWARD = Path("reports/alert_fade/forward.csv")
BORROW = Path("data/borrow")
DAILY = Path("data/alert_daily_forward")
FIRST = "2026-11-04"                           # first session with stored borrow snapshots
USD, MIN_EVENTS, ALPHA, SEED, N_BOOT = 2500.0, 300, 0.05 / 2, 20261117, 10_000
CLOSE_HOUR, MAX_BEFORE_CLOSE = 16, timedelta(hours=3)
CLIP = (-0.95, 3.0)
HORIZONS = {"S1": 1, "S5": 5}


def closing_snapshot(session: str, root: Path = BORROW) -> pd.DataFrame | None:
    """The last stored IBKR list stamped at or before 16:00 ET of ``session`` (and within 3 hours of it), indexed by
    symbol with ``avail`` and ``fee``; None when there is none (borrow unknown that day)."""
    d = date.fromisoformat(session)
    close = datetime(d.year, d.month, d.day, CLOSE_HOUR, tzinfo=ET)
    best = None
    for day in {close.astimezone(timezone.utc).date(), (close - MAX_BEFORE_CLOSE).astimezone(timezone.utc).date()}:
        folder = root / f"{day:%Y%m%d}"
        if not folder.is_dir():
            continue
        for p in folder.glob("*.parquet"):
            try:
                at = datetime.strptime(folder.name + p.stem, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if close - MAX_BEFORE_CLOSE <= at <= close and (best is None or at > best[0]):
                best = (at, p)
    if best is None:
        return None
    df = pd.read_parquet(best[1], columns=["symbol", "fee", "avail"])
    return df.drop_duplicates("symbol").set_index("symbol")


def lendable(snap: pd.DataFrame | None, symbol: str, close: float) -> tuple[bool | None, float | None]:
    """(enough shares for the position, annual fee %) at one close; (None, None) when borrow is unknown."""
    if snap is None or not close > 0:
        return None, None
    if symbol not in snap.index:
        return False, None
    r = snap.loc[symbol]
    fee = None if pd.isna(r.fee) else float(r.fee)
    return bool(float(r.avail) >= math.ceil(USD / close)), fee


def trade(daily: pd.DataFrame, snaps, symbol: str, day: str, cost: float, hold: int) -> dict | None:
    """Short at the close of ``day``, cover ``hold`` sessions later or at the first earlier close without the shares.
    None when the day or its following sessions are not in ``daily``; ``borrowable`` False/None = not an event."""
    if day not in daily.index:
        return None
    i = daily.index.get_loc(day)
    if not isinstance(i, int) or i + max(HORIZONS.values()) >= len(daily):
        return None
    entry = float(daily.close.iloc[i])
    ok, fee = lendable(snaps(day), symbol, entry)
    out = {"symbol": symbol, "session": day, "borrowable": ok, "fee_entry": fee}
    j, fees, recalled = i + hold, [fee or 0.0], False
    for k in range(i + 1, i + hold + 1):
        d = str(daily.index[k])
        if k == i + hold:
            j = k
            break
        still, f = lendable(snaps(d), symbol, float(daily.close.iloc[k]))
        fees.append(f or 0.0)
        if not still:                                      # shares gone or unknown at this close: covered here
            j, recalled = k, True
            break
    cover = float(daily.close.iloc[j])
    days = (date.fromisoformat(str(daily.index[j])) - date.fromisoformat(day)).days
    ret = float(np.clip(cover / entry - 1, *CLIP))
    carry = max(fees) / 100.0 / 360.0 * days
    out.update(ret=ret, carry=carry, net=-ret - cost - carry, recalled=recalled, days=days, cover_day=str(daily.index[j]))
    return out


def forward_events(path: Path = FORWARD) -> pd.DataFrame:
    e = pd.read_csv(path, usecols=["symbol", "session", "cost_rt"])
    e["session"] = e.session.astype(str)
    return e[e.session >= FIRST].drop_duplicates(["symbol", "session"]).reset_index(drop=True)


def count(events: pd.DataFrame, daily_of, snaps) -> dict:
    """Eligibility only: no return is computed or returned."""
    n = {"alert_days": int(len(events)), "with_bars_and_5_sessions": 0, "borrow_known": 0, "borrowable": 0}
    for r in events.itertuples(index=False):
        d = daily_of(r.symbol)
        if d is None or r.session not in d.index:
            continue
        i = d.index.get_loc(r.session)
        if not isinstance(i, int) or i + 5 >= len(d):
            continue
        n["with_bars_and_5_sessions"] += 1
        ok, _ = lendable(snaps(r.session), r.symbol, float(d.close.iloc[i]))
        n["borrow_known"] += ok is not None
        n["borrowable"] += ok is True
    return n


def bound(v: np.ndarray, s: np.ndarray) -> float:
    g = pd.DataFrame({"v": v, "s": s}).groupby("s").v.agg(["sum", "count"])
    idx = np.random.default_rng(SEED).integers(0, len(g), size=(N_BOOT, len(g)))
    return float(np.quantile(g["sum"].to_numpy()[idx].sum(1) / g["count"].to_numpy()[idx].sum(1), ALPHA))


def decide(rows: pd.DataFrame) -> dict:
    """``rows``: one trade per (event, horizon) with borrowable, net, recalled, carry."""
    out = {}
    for h in HORIZONS:
        t = rows[rows.horizon == h]
        b, nb = t[t.borrowable == True], t[t.borrowable == False]  # noqa: E712
        r = {"borrowable_events": int(len(b)), "not_borrowable_events": int(len(nb)),
             "borrowable_share": float(len(b) / max(1, len(b) + len(nb)))}
        if len(b):
            lo = bound(b.net.to_numpy(float), b.session.to_numpy()) if len(b) >= 2 else float("nan")
            r.update(mean_net=float(b.net.mean()), median_net=float(b.net.median()), win=float((b.net > 0).mean()),
                     lower=lo, recalled=float(b.recalled.mean()), mean_carry=float(b.carry.mean()),
                     lose_over_20pct=float((b.net < -0.20).mean()),
                     worst=[[x.symbol, x.session, round(float(x.net), 4)] for x in b.nsmallest(5, "net").itertuples()],
                     **{"pass": bool(len(b) >= MIN_EVENTS and b.net.mean() > 0 and lo > 0)})
        else:
            r["pass"] = False
        r["not_borrowable_mean_net"] = float(nb.net.mean()) if len(nb) else None
        out[h] = r
    return out


def daily_loader(today: date):
    """Adjusted daily bars per symbol through ``today`` (fetched once per run into its own cache)."""
    import alert_after_day_study as base

    base.CACHE, base.END = DAILY / today.isoformat(), today.isoformat()
    memo: dict[str, pd.DataFrame | None] = {}

    def daily_of(sym: str):
        if sym not in memo:
            memo[sym] = base.fetch(sym)
        return memo[sym]
    return daily_of


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", action="store_true", help="eligibility counts only; computes no outcome")
    a = ap.parse_args(argv)
    res_path = OUT / "result.json"
    if not a.count and res_path.is_file():
        print(f"{res_path} exists: the decision was made once and is not repeated")
        return 2
    ev = forward_events()
    daily_of = daily_loader(datetime.now(ET).date())
    memo: dict[str, pd.DataFrame | None] = {}

    def snaps(session: str):
        if session not in memo:
            memo[session] = closing_snapshot(session)
        return memo[session]

    n = count(ev, daily_of, snaps)
    if a.count or n["borrowable"] < MIN_EVENTS:
        print(json.dumps({"count": n, "needed": MIN_EVENTS,
                          "note": "no outcome is computed before the count is reached"}, indent=1))
        return 0 if a.count else 3
    rows = []
    for r in ev.itertuples(index=False):
        d = daily_of(r.symbol)
        if d is None:
            continue
        for h, hold in HORIZONS.items():
            t = trade(d, snaps, r.symbol, r.session, float(r.cost_rt), hold)
            if t is not None and t["borrowable"] is not None:
                rows.append({**t, "horizon": h})
    res = {"prereg": "docs/findings/prereg_after_day_borrow_forward_2026-11-17.md", "count": n,
           "horizons": decide(pd.DataFrame(rows))}
    OUT.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
