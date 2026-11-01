"""Replay the trader's entry on LIVE candles (SIP ticks) on in-play gap days.

Ticks are fetched only for minutes where an intrabar trigger is possible (completed-bar
state below the cross, the minute's final volume reaches the spike or a recent spike
exists, and the minute's HIGH would push provisional EMA9 above EMA24). Cached under
data/ticks/. Training period only (--before); the lockbox stays untouched.

Usage:
    python scripts/intrabar_study.py --before 2026-04-01 --dry-run
    python scripts/intrabar_study.py --before 2026-04-01 --latency 1.0
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.config import get_settings
from sgt.core.types import Segment
from sgt.decision import validation as val
from sgt.decision.features import CostModelAdapter
from sgt.decision.intrabar import _A9, _A24, bar_state, clean_trades, fill_after, first_trigger
from sgt.decision.trader_rules import indicators
from sgt.intraday.sources import _alpaca_headers
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("intrabar_study")
WINDOWS = {"open_hour": (570, 629), "midday": (630, 899), "power_hour": (900, 950)}
EXITS = ("trader", "ema9", "hold5")
RULES = dict(prior_gap_pct=0.005, lookback=15, spike_lookback=5, vol_spike=5.0, rsi_max=70.0,
             require_macd_pos=True, require_pvt_green=True)


def window_of(minute: int) -> str | None:
    for name, (a, b) in WINDOWS.items():
        if a <= minute <= b:
            return name
    return None


def candidate_minutes(day: pd.DataFrame, ind: pd.DataFrame, rth: np.ndarray) -> list[int]:
    e9, e24 = ind["ema9"].to_numpy(), ind["ema24"].to_numpy()
    hi, v = day["high"].to_numpy(), day["volume"].to_numpy()
    base = ind["vol_base"].to_numpy()
    gap = ind["gap_pct"].to_numpy()
    ratio = v / base
    out = []
    for m in range(30, len(day)):
        if not rth[m] or window_of(int(day["minute_of_day"].iloc[m])) is None:
            continue
        if e9[m - 1] > e24[m - 1] or not np.nanmin(gap[max(0, m - 15):m]) <= -RULES["prior_gap_pct"]:
            continue
        spike = np.nanmax(np.r_[ratio[max(0, m - 4):m], ratio[m]])
        if not spike >= RULES["vol_spike"]:
            continue
        p = hi[m]
        if e9[m - 1] + _A9 * (p - e9[m - 1]) > e24[m - 1] + _A24 * (p - e24[m - 1]):
            out.append(m)
    return out


class TickCache:
    def __init__(self, root: Path, client: httpx.Client) -> None:
        self.root, self.client = root, client
        self.url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/trades"

    def minute(self, sym: str, ts_open) -> np.ndarray:
        p = self.root / sym / f"{ts_open:%Y%m%d%H%M}.npy"
        if p.exists():
            return np.load(p)
        start, end = ts_open, ts_open + timedelta(minutes=1)
        rows, token = [], None
        for _ in range(50):
            params = {"symbols": sym, "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "feed": "sip", "limit": 10000}
            if token:
                params["page_token"] = token
            for attempt in range(6):
                try:
                    r = self.client.get(self.url, params=params, headers=_alpaca_headers())
                except httpx.TransportError:
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code == 200:
                    break
                if r.status_code == 429 or r.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise SystemExit(f"trades HTTP {r.status_code}: {r.text[:200]!r}")
            else:
                raise SystemExit(f"trades for {sym} {ts_open} unreachable after retries")
            j = r.json()
            rows += (j.get("trades") or {}).get(sym, []) or []
            token = j.get("next_page_token")
            time.sleep(0.3)
            if not token:
                break
        arr = clean_trades(rows)
        p.parent.mkdir(parents=True, exist_ok=True)
        np.save(p, arr)
        return arr


def manage(day, ind, m, entry, stop, ticks_after, last_i, rule) -> tuple[float, int, str]:
    """Exit from a fill inside minute m. Intrabar stop on the remaining ticks of m, then
    completed-bar exits from bar m on (signal at close -> next open)."""
    hit = np.flatnonzero(ticks_after[:, 1] <= stop) if len(ticks_after) else np.array([])
    if hit.size:
        return float(ticks_after[hit[0], 1]), m, "stop"
    o, l, c, v = (day[k].to_numpy(float) for k in ("open", "low", "close", "volume"))
    hist, rsi, e9, e24 = (ind[k].to_numpy(float) for k in ("hist", "rsi", "ema9", "ema24"))
    hot = False
    for j in range(m, len(day)):
        if j > m and l[j] <= stop:
            return (o[j] if o[j] < stop else stop), j, "stop"
        if j >= last_i:
            return c[j], j, "close"
        if j + 1 >= len(day):
            break
        if rule == "hold5":
            if j >= m + 5:
                return o[j + 1], j + 1, "time"
            continue
        if rule == "ema9":
            if c[j] < e9[j]:
                return o[j + 1], j + 1, "ema9"
            continue
        hot |= rsi[j] >= 70
        if (hist[j] > 0 and hist[j] < hist[j - 1]) or (hot and rsi[j] < 70) or e9[j] < e24[j] \
                or (j >= m + 2 and c[j] > c[j - 2] and v[j] < v[j - 1] < v[j - 2]):
            return o[j + 1], j + 1, "fade"
    return c[-1], len(day) - 1, "close"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gappers", type=Path, default=Path("data/universe/gappers_2y.csv"))
    ap.add_argument("--before", type=date.fromisoformat, required=True)
    ap.add_argument("--after", type=date.fromisoformat, default=date(1900, 1, 1))
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    ap.add_argument("--latency", type=float, default=1.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", type=Path, default=Path("reports/decision/intrabar"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    days: dict[str, list[date]] = defaultdict(list)
    with args.gappers.open() as fh:
        for r in csv.DictReader(fh):
            d = date.fromisoformat(r["session"])
            if float(r["prev_volume"]) >= args.min_prev_volume and args.after <= d < args.before:
                days[r["symbol"].upper()].append(d)
    store = ParquetBarStore("data/alpaca")
    client = httpx.Client(timeout=get_settings().http_timeout_s)
    cache = TickCache(Path("data/ticks"), client)
    n_cand, rows = 0, []
    for k, (sym, ds) in enumerate(sorted(days.items()), 1):
        for d in ds:
            bars = store.read(sym, d, d)
            if len(bars) < 60:
                continue
            day = pd.DataFrame({"open": [b.open for b in bars], "high": [b.high for b in bars],
                                "low": [b.low for b in bars], "close": [b.close for b in bars],
                                "volume": [b.volume for b in bars],
                                "minute_of_day": [b.minute_of_day for b in bars]})
            rth = np.array([b.segment is Segment.RTH for b in bars])
            if not rth.any():
                continue
            last_i = int(np.flatnonzero(rth)[-1])
            ind = indicators(day)
            cands = candidate_minutes(day, ind, rth)
            n_cand += len(cands)
            if args.dry_run:
                continue
            busy = {rule: -1 for rule in EXITS}
            for m in cands:
                if all(m <= b for b in busy.values()):
                    continue
                st = bar_state(day, m, RULES["prior_gap_pct"], RULES["lookback"], RULES["spike_lookback"])
                if st is None:
                    continue
                ticks = cache.minute(sym, bars[m].ts)
                if len(ticks) == 0:
                    continue
                kt = first_trigger(st, ticks, vol_spike=RULES["vol_spike"], rsi_max=RULES["rsi_max"],
                                   require_macd_pos=RULES["require_macd_pos"],
                                   require_pvt_green=RULES["require_pvt_green"])
                if kt is None:
                    continue
                kf = fill_after(ticks, kt, args.latency)
                if kf is None:
                    entry, after = float(day["open"].iloc[m + 1]) if m + 1 < len(day) else np.nan, ticks[:0]
                else:
                    entry, after = float(ticks[kf, 1]), ticks[kf + 1:]
                stop = float(min(day["low"].iloc[m - 2:m].min(), ticks[:kt + 1, 1].min()))
                if not entry > stop:
                    continue
                trig_t = pd.Timestamp(int(ticks[kt, 0]), tz="UTC")
                for rule in EXITS:
                    if m <= busy[rule]:
                        continue
                    px, j, why = manage(day, ind, m, entry, stop, after, last_i, rule)
                    busy[rule] = j
                    hin = 0.5 * cost.round_trip(sym, trig_t, entry)[0]
                    hout = 0.5 * cost.round_trip(sym, bars[min(j, len(bars) - 1)].ts, px)[0]
                    rows.append({"symbol": sym, "session_date": d.isoformat(), "minute": int(day["minute_of_day"].iloc[m]),
                                 "window": window_of(int(day["minute_of_day"].iloc[m])), "exit_rule": rule,
                                 "why": why, "entry": entry, "exit": px, "stop": stop,
                                 "late_by_close": float(day["close"].iloc[m]) / entry - 1,
                                 "gross": px / entry - 1,
                                 "net": (px * (1 - hout)) / (entry * (1 + hin)) - 1})
        if k % 100 == 0:
            log.info("%d/%d symbols; candidates=%d trades=%d", k, len(days), n_cand, len(rows))
    client.close()
    if args.dry_run:
        print(f"candidate minutes needing ticks: {n_cand}")
        return 0
    df = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out.with_suffix(".trades.csv"), index=False)
    lines = [f"# Live-candle (intrabar) replay, latency {args.latency}s, {args.after}..{args.before}", "",
             "| window | exit | trades | gross | net | hit | avg win | avg loss | 90% CI net (day-clustered) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for (w, rule), x in df.groupby(["window", "exit_rule"]):
        wn, ls = x.net[x.net > 0], x.net[x.net <= 0]
        ci = val.cluster_bootstrap_ci(x.net.to_numpy(), x.session_date.to_numpy(), alpha=0.10)
        lines.append(f"| {w} | {rule} | {len(x)} | {100 * x.gross.mean():+.3f}% | {100 * x.net.mean():+.3f}% | "
                     f"{(x.net > 0).mean():.0%} | {100 * wn.mean():+.2f}% | {100 * ls.mean():+.2f}% | "
                     f"[{100 * ci[0]:+.3f}%, {100 * ci[1]:+.3f}%] |")
    report = "\n".join(lines) + "\n"
    args.out.with_suffix(".report.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
