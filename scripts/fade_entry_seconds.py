"""How many seconds after the alert should the short be entered? (docs/findings/prereg_fade_entry_seconds_2026-11-17.md)

    .venv/bin/python scripts/fade_entry_seconds.py --fetch      # official prints of each alert's minutes (cached, throttled)
    .venv/bin/python scripts/fade_entry_seconds.py              # training, run ONCE

Report only: nothing here changes a book. The print API is external: rows are cleaned by ``clean_trades`` and bounded."""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from fade_challengers import _rth, events
from sgt.fast import fade
from sgt.fast.exits import CHAMPION, indicators, simulate
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/fade_entry_seconds")
HALTS = Path("reports/fade_halt_audit/halt_events.csv")
TICKS = Path("data/ticks")
SECONDS = (10, 15, 20, 30, 45, 60)
TOL = pd.Timedelta(seconds=10)                 # a market order needs a print this soon after its instant
THROTTLE_S = 0.45                              # on top of the cache's own pause: about one request a second, so the
                                               # runner (same data account, 200 a minute) is never starved
ALPHA, SEED, N_BOOT, TAIL_PP = 0.05, 20261117, 10_000, 0.05
ET = "America/New_York"
NS = 1_000_000_000


def all_events(split: str = "train") -> pd.DataFrame:
    """The tested fades of the split plus the alerts that ran into a halt (the champion does not trade those)."""
    ok = events(split).assign(kind="ok")
    cols = ["symbol", "session", "alert_type", "posted_at", "entry_ts", "entry", "cost_rt", "kind"]
    parts = [ok[cols]]
    if HALTS.is_file():
        h = pd.read_csv(HALTS)
        parts.append(h[h.split == split].assign(kind="halt")[cols])
    ev = pd.concat(parts, ignore_index=True)
    ev["posted_at"] = pd.to_datetime(ev.posted_at, utc=True)
    ev["session"] = ev.session.astype(str)
    return ev.sort_values(["posted_at", "symbol"], kind="stable").reset_index(drop=True)


def minutes_needed(posted: pd.Timestamp) -> list[pd.Timestamp]:
    """Minute starts whose prints can hold an entry for some s: from the alert's minute to the one holding
    alert + max(s) + tolerance."""
    first = posted.floor("min")
    last = (posted + pd.Timedelta(seconds=max(SECONDS)) + TOL).floor("min")
    return list(pd.date_range(first, last, freq="1min"))


def prints(cache, sym: str, posted: pd.Timestamp) -> np.ndarray:
    """Official prints of the event's minutes as rows (ns, price, size), time-ordered."""
    parts = [cache.minute(sym, m.to_pydatetime()) for m in minutes_needed(posted)]
    parts = [p for p in parts if p is not None and len(p)]
    if not parts:
        return np.empty((0, 3))
    a = np.concatenate(parts)
    return a[np.argsort(a[:, 0], kind="stable")]


def first_print(tr: np.ndarray, at: pd.Timestamp) -> tuple[int, float] | None:
    """The first print at or after ``at``, if it comes within TOL: (its row index, price)."""
    if not len(tr):
        return None
    t0 = at.value
    i = int(np.searchsorted(tr[:, 0], t0, side="left"))
    if i >= len(tr) or tr[i, 0] > t0 + TOL.value or not tr[i, 1] > 0:
        return None
    return i, float(tr[i, 1])


def entry_bar(tr: np.ndarray, i: int) -> dict:
    """The entry minute rebuilt from the prints AT OR AFTER the entry: nothing earlier can stop the trade."""
    start = pd.Timestamp(int(tr[i, 0]), tz="UTC").floor("min")
    rest = tr[i:][tr[i:, 0] < (start + pd.Timedelta(minutes=1)).value]
    px = rest[:, 1]
    return {"ts": start, "open": float(px[0]), "high": float(px.max()), "low": float(px.min()),
            "close": float(px[-1]), "volume": float(rest[:, 2].sum())}


def variant(bars: pd.DataFrame, tr: np.ndarray, posted: pd.Timestamp, s: int, session: str) -> dict | None:
    """The short entered ``s`` seconds after the alert, then the champion's stop and hour. None = no trade."""
    hit = first_print(tr, posted + pd.Timedelta(seconds=s))
    if hit is None:
        return None
    i, px = hit
    first = entry_bar(tr, i)
    et = first["ts"].tz_convert(ET).time()
    if not fade.FIRST_ENTRY <= et <= fade.last_entry(date.fromisoformat(session)) or not fade.PRICE_MIN <= px <= fade.PRICE_MAX:
        return None
    later = bars[bars.ts > first["ts"]][["ts", "open", "high", "low", "close", "volume"]]
    f = pd.concat([pd.DataFrame([first]), later], ignore_index=True)
    r = simulate(indicators(f), first["ts"], px, CHAMPION)
    if r is None:
        return None
    return {"gross": float(r["gross"]), "how": r["how"], "entry": px, "at": pd.Timestamp(int(tr[i, 0]), tz="UTC")}


def collect(ev: pd.DataFrame, store: ParquetBarStore, cache) -> pd.DataFrame:
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, r.session)
        if bars.empty:
            continue
        tr = prints(cache, r.symbol, r.posted_at)
        row = {"symbol": r.symbol, "session": r.session, "kind": r.kind, "second": int(r.posted_at.second),
               "cost": float(r.cost_rt), "bar_entry": float(r.entry) if r.kind == "ok" else np.nan}
        if r.kind == "ok":
            c = simulate(indicators(bars), pd.Timestamp(r.entry_ts), float(r.entry), CHAMPION)
            row["net_champion"] = np.nan if c is None else c["gross"] - row["cost"] - LOCATE
            row["how_champion"] = None if c is None else c["how"]
        else:
            row["net_champion"], row["how_champion"] = np.nan, None           # the bar rule does not trade a halt
        for s in SECONDS:
            v = variant(bars, tr, r.posted_at, s, r.session)
            row[f"net_S{s}"] = np.nan if v is None else v["gross"] - row["cost"] - LOCATE
            row[f"how_S{s}"] = None if v is None else v["how"]
            row[f"px_S{s}"] = np.nan if v is None else v["entry"]
        rows.append(row)
    return pd.DataFrame(rows)


def paired(t: pd.DataFrame, col: str, alpha: float) -> dict:
    """Total net per session, a leg that does not trade counting 0; paired difference against the champion."""
    d = t[["session"]].assign(a=t[col].fillna(0.0), c=t.net_champion.fillna(0.0)).groupby("session")[["a", "c"]].sum()
    diff = (d.a - d.c).to_numpy(float)
    idx = np.random.default_rng(SEED).integers(0, len(diff), size=(N_BOOT, len(diff)))
    means = diff[idx].mean(1)
    x, c = t[col].dropna(), t.net_champion.dropna()
    return {"sessions": int(len(diff)), "mean_daily_diff": float(diff.mean()), "lower": float(np.quantile(means, alpha)),
            "upper": float(np.quantile(means, 1 - alpha)), "trades": int(len(x)), "mean": float(x.mean()) if len(x) else None,
            "win": float((x > 0).mean()) if len(x) else None, "p01": float(np.quantile(x, 0.01)) if len(x) else None,
            "total": float(x.sum()), "champ_trades": int(len(c)), "champ_mean": float(c.mean()), "champ_total": float(c.sum()),
            "champ_p01": float(np.quantile(c, 0.01))}


def evaluate(t: pd.DataFrame) -> dict:
    alpha = ALPHA / len(SECONDS)
    res = {}
    for s in SECONDS:
        col = f"net_S{s}"
        p = paired(t, col, alpha)
        p["pass"] = bool(p["lower"] > 0 and p["p01"] is not None and p["p01"] >= p["champ_p01"] - TAIL_PP)
        p["stopped"] = float((t[f"how_S{s}"] == "stop").sum() / max(1, int(t[col].notna().sum())))
        ok, halt = t[t.kind == "ok"], t[t.kind == "halt"]
        both = ok.dropna(subset=[col, "net_champion"])
        ratio = (both[f"px_S{s}"] / both.bar_entry - 1)
        p["ok_events"] = {"both_traded": int(len(both)), "variant_mean": float(both[col].mean()) if len(both) else None,
                          "champion_mean": float(both.net_champion.mean()) if len(both) else None,
                          "entry_vs_bar_mean": float(ratio.mean()) if len(both) else None,
                          "entry_vs_bar_median": float(ratio.median()) if len(both) else None,
                          "variant_no_print": int(ok[col].isna().sum())}
        hx = halt[col].dropna()
        p["halt_events"] = {"events": int(len(halt)), "traded": int(len(hx)), "mean": float(hx.mean()) if len(hx) else None,
                            "total": float(hx.sum())}
        by = {}
        for name, lo, hi in (("alert second 0-19", 0, 20), ("20-39", 20, 40), ("40-59", 40, 60)):
            g = both[(both.second >= lo) & (both.second < hi)]
            by[name] = {"n": int(len(g)), "variant_minus_champion": float((g[col] - g.net_champion).mean()) if len(g) else None}
        p["by_alert_second"] = by
        res[f"S{s}"] = p
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="only fetch and cache the prints (no number is computed)")
    a = ap.parse_args(argv)
    res_path = OUT / "result_train.json"
    if not a.fetch and res_path.is_file():
        print(f"{res_path} exists: the training run was made once and is not repeated")
        return 2
    import httpx

    from intrabar_study import TickCache

    ev = all_events("train")
    with httpx.Client(timeout=30) as client:
        cache = TickCache(TICKS, client)
        if a.fetch:
            n = 0
            for r in ev.itertuples(index=False):
                for m in minutes_needed(r.posted_at):
                    if not (TICKS / r.symbol / f"{m:%Y%m%d%H%M}.npy").exists():
                        cache.minute(r.symbol, m.to_pydatetime())
                        n += 1
                        time.sleep(THROTTLE_S)
                        if n % 250 == 0:
                            print(f"fetched {n} minutes", flush=True)
            print(f"done: {n} minutes fetched, {len(ev)} events", flush=True)
            return 0
        from alert_fade_test import score as research_score

        store = ParquetBarStore("data/alpaca")
        t = collect(ev, store, cache)
    ok = events("train")
    ref = research_score(ok, store).set_index(["symbol", "session"]).net
    mine = t[t.kind == "ok"].set_index(["symbol", "session"]).net_champion.reindex(ref.index)
    if not np.allclose(mine.to_numpy(float), ref.to_numpy(float), equal_nan=True, rtol=0, atol=1e-12):
        raise SystemExit("champion does not reproduce alert_fade_test.score: nothing reported")
    res = {"prereg": "docs/findings/prereg_fade_entry_seconds_2026-11-17.md", "events": int(len(t)),
           "ok": int((t.kind == "ok").sum()), "halts": int((t.kind == "halt").sum()), "variants": evaluate(t)}
    res["passed"] = [k for k, v in res["variants"].items() if v["pass"]]
    OUT.mkdir(parents=True, exist_ok=True)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    t.to_csv(OUT / "train_events.csv", index=False)
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
