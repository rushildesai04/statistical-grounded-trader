"""Are fades on EARLY alerts the losing third? The forward scorer
(docs/findings/prereg_early_alert_fade_forward_2026-11-18.md).

    python scripts/early_alert_forward.py night     # the runner, nightly: the count; the decision on the first night at 300
    python scripts/early_alert_forward.py count     # the count only (nothing is fetched or written)

Report only: nothing here changes a book. Before the decision only COUNTS are read: no result by band is computed.
The decision is made once (``result.json``) and never repeated. Headlines come from an external API: read as data
by the live rule's own function, bounded, with a timeout."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
OUT = Path("reports/early_alert_forward")
OPS = Path("reports/ops/ops.db")
FIRST = "2026-11-04"                            # the first session of the forward sample
MIN_N, ALPHA, SEED, N_BOOT = 300, 0.05, 20261118, 10_000
BANDS = ("early", "middle", "late")
NO_BAND = "none"                                # official bars give no label (no start bar, too few bars): final
MAX_ROWS, MAX_FILL, THROTTLE_S = 50_000, 100, 0.5
SYM = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")
COLS = ["symbol", "session", "alert_type", "net", "scen_band"]
FILL_COLS = ["symbol", "key", "news_own", "scen_band"]


def labels(path: Path = OPS) -> pd.DataFrame:
    """What the live system stamped on each alert since FIRST: the news flag at the alert and the band from official
    bars (written once, the night after). Read-only. One row per symbol and posting second: two alerts in one second
    share a start bar, and any company-news flag among them counts."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)) as c:
        df = pd.read_sql("SELECT symbol, posted_at, news_own, scen_band FROM fade_trades WHERE session>=? ORDER BY id LIMIT ?",
                         c, params=(FIRST, MAX_ROWS))
    df["key"] = pd.to_datetime(df.posted_at, utc=True, format="ISO8601", errors="coerce").dt.floor("s")
    df = df.dropna(subset=["key"]).assign(symbol=lambda d: d.symbol.astype(str))
    return df.groupby(["symbol", "key"], as_index=False).agg(news_own=("news_own", "max"), scen_band=("scen_band", "first"))


def load_fills(out: Path = OUT) -> pd.DataFrame:
    p = out / "fills.csv"
    if not p.is_file():
        return pd.DataFrame(columns=FILL_COLS)
    f = pd.read_csv(p, converters={"symbol": str, "scen_band": str})
    return f.assign(key=pd.to_datetime(f.key, utc=True, format="ISO8601"))


def band_of(day: pd.DataFrame | None, posted: pd.Timestamp) -> str:
    """The band of an alert from its day's official bars, exactly as the nightly label is made (sgt.fast.scenario)."""
    from sgt.fast import scenario

    if day is None or not len(day):
        return NO_BAND
    day = day.assign(ts=pd.to_datetime(day.ts, utc=True)).sort_values("ts").reset_index(drop=True)
    t0 = scenario.start_bar(day, posted)
    st = scenario.state(day, t0) if t0 is not None else None
    return NO_BAND if st is None else scenario.band(st["run"])


def resolve(scored: pd.DataFrame, lab: pd.DataFrame, fills: pd.DataFrame, news_fn=None, band_fn=None,
            max_new: int = MAX_FILL) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Band and news flag of every scored forward event (symbol, session, alert_type, posted_at, net) from FIRST on.
    The live system's own stamp on the SAME alert (symbol and posting second) comes first; what it never stamped is
    filled here once and kept (``fills``): the band from official bars, the news flag from the live rule's own
    lookup at the alert's posting time. A news alert is news. With no ``news_fn`` / ``band_fn`` nothing is filled.
    Returns (the test's sample, the fills, counts). The sample: a result, a band, and no company news."""
    todo = {"news_unknown": 0, "band_unknown": 0, "filled_now": 0}
    if scored.empty:
        return pd.DataFrame(columns=COLS), fills, todo
    s = scored.assign(session=scored.session.astype(str),
                      key=pd.to_datetime(scored.posted_at, utc=True, format="ISO8601").dt.floor("s"))
    s = s[s.session >= FIRST].dropna(subset=["net"]).drop_duplicates(["symbol", "session"])
    known = {(r.symbol, r.key): (r.news_own, r.scen_band) for r in lab.itertuples(index=False)}
    mine = {(r.symbol, r.key): [r.news_own, r.scen_band] for r in fills.itertuples(index=False)}
    rows, asked = [], 0
    for r in s.itertuples(index=False):
        k = (r.symbol, r.key)
        news, band = known.get(k, (None, None))
        m = mine.setdefault(k, [None, None])
        band = band if isinstance(band, str) and band else (m[1] if isinstance(m[1], str) and m[1] else None)
        if band is None and band_fn is not None:
            band = m[1] = band_fn(r.symbol, r.session, r.key)
            todo["filled_now"] += 1
        news = 1 if r.alert_type == "news" else news if pd.notna(news) else m[0] if pd.notna(m[0]) else None
        if news is None and news_fn is not None and asked < max_new:
            asked += 1
            got = news_fn(r.symbol, r.key)
            if got is not None:
                news = m[0] = int(bool(got))
                todo["filled_now"] += 1
        todo["news_unknown"] += news is None
        todo["band_unknown"] += band is None
        if band in BANDS and news is not None and int(news) == 0:
            rows.append({"symbol": r.symbol, "session": r.session, "alert_type": r.alert_type, "net": float(r.net), "scen_band": band})
    out = pd.DataFrame([{"symbol": a, "key": b, "news_own": v[0], "scen_band": v[1]} for (a, b), v in mine.items()
                        if pd.notna(v[0]) or v[1]], columns=FILL_COLS)
    return pd.DataFrame(rows, columns=COLS), out, {k: int(v) for k, v in todo.items()}


def count(s: pd.DataFrame, todo: dict | None = None) -> dict:
    """How far the test is. No result is read."""
    return {"alerts_with_result_and_band": int(len(s)), "sessions": int(s.session.nunique()),
            "by_band": {b: int((s.scen_band == b).sum()) for b in BANDS}, "needed": MIN_N, **(todo or {})}


def decide(s: pd.DataFrame) -> dict:
    """(mean net of the NOT-EARLY alerts) minus (mean net of the EARLY alerts): one-sided lower bound, bootstrap by
    session. PASS = the bound is above 0 AND the EARLY alerts' own mean net is below 0. No bound = fail."""
    x, e = s.net.to_numpy(float), (s.scen_band == "early").to_numpy()
    u, inv = np.unique(s.session.to_numpy(), return_inverse=True)
    out = {"alerts": int(len(x)), "sessions": int(len(u)), "early": int(e.sum()), "not_early": int((~e).sum()),
           "mean_early": float(x[e].mean()) if e.any() else None, "mean_not_early": float(x[~e].mean()) if (~e).any() else None,
           "difference": None, "lower": None, "pass": False}
    if len(u) < 2 or not e.any() or e.all():
        return out
    se, ne = np.bincount(inv, x * e, len(u)), np.bincount(inv, e.astype(float), len(u))
    sr, nr = np.bincount(inv, x * ~e, len(u)), np.bincount(inv, (~e).astype(float), len(u))
    idx = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    de, dr = ne[idx].sum(1), nr[idx].sum(1)
    ok = (de > 0) & (dr > 0)
    out["difference"] = out["mean_not_early"] - out["mean_early"]
    if ok.any():
        out["lower"] = float(np.quantile(sr[idx].sum(1)[ok] / dr[ok] - se[idx].sum(1)[ok] / de[ok], ALPHA))
        out["pass"] = bool(out["lower"] > 0 and out["mean_early"] < 0)
    return out


def night(s: pd.DataFrame, todo: dict | None = None, out: Path = OUT) -> dict:
    """The nightly step: the count; on the first night with MIN_N alerts, the decision, written once."""
    n, res_path = count(s, todo), out / "result.json"
    if res_path.is_file():
        return {"count": n, "note": "decided earlier; the decision is not repeated"}
    if n["alerts_with_result_and_band"] < MIN_N:
        return {"count": n, "note": "no result by band is computed before the count is reached"}
    res = {"prereg": "docs/findings/prereg_early_alert_fade_forward_2026-11-18.md", "count": n, **decide(s)}
    out.mkdir(parents=True, exist_ok=True)
    s.to_csv(out / "sample.csv", index=False)
    res_path.write_text(json.dumps(res, indent=1) + "\n")
    return res


def news_flag(client, symbol: str, at: pd.Timestamp) -> int | None:
    """1 = the company had news before the alert, 0 = none, None = unknown (asked again another night). The same
    request and the same labelling as the live rule (scripts/fade_live.py ``live_news``, sgt.fast.news.flags)."""
    from sgt.fast import news

    if not SYM.fullmatch(symbol):
        return None
    at = at.to_pydatetime()
    try:
        r = client.get("/v1beta1/news", params={"symbols": symbol, "start": news.window_start(at).isoformat(),
                                                "end": at.isoformat(), "limit": 50, "sort": "desc"})
        items = r.json().get("news") if r.status_code == 200 else None
    except Exception as e:  # noqa: BLE001 - network or JSON: unknown, said in the log, asked again another night
        print(f"early-alert forward: news of {symbol} not read ({type(e).__name__})")
        return None
    finally:
        time.sleep(THROTTLE_S)
    if not isinstance(items, list):
        print(f"early-alert forward: news of {symbol} not read (no list in the answer)")
        return None
    rows = [{"created_at": a.get("created_at"), "symbols": ",".join(str(x) for x in (a.get("symbols") or [])[:20]),
             "headline": news.clean(a.get("headline"))} for a in items[:50] if isinstance(a, dict)]
    f = news.flags(symbol, at, pd.DataFrame(rows, columns=["created_at", "symbols", "headline"]))
    return int(bool(f.get("news_own") or f.get("news_earn")))


def scored_events(store) -> pd.DataFrame:
    """The forward record's own events and scores (the same code as scripts/alert_fade_forward.py; stored bars only),
    with each event's posting time."""
    import alert_fade_forward as fwd
    from alert_fade_test import score
    from megacap_forward import last_complete_session
    from telegram_alert_test import load_alerts

    ev = fwd.forward_events(load_alerts(fwd.DB, fwd.CHANNEL), store, last_complete_session(datetime.now(ET)), fill_bars=False)
    if ev.empty:
        return pd.DataFrame(columns=["symbol", "session", "alert_type", "posted_at", "net"])
    return score(ev, store).merge(ev[["symbol", "session", "posted_at"]], on=["symbol", "session"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("night", "count"))
    a = ap.parse_args(argv)
    from sgt.intraday.store import ParquetBarStore

    store = ParquetBarStore("data/alpaca")
    scored, lab, fills = scored_events(store), labels(), load_fills()
    if a.cmd == "count":
        s, _, todo = resolve(scored, lab, fills)
        print("early-alert forward: " + json.dumps(count(s, todo)))
        return 0
    import httpx

    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    def band_fn(symbol: str, session: str, key: pd.Timestamp) -> str:
        d = date.fromisoformat(session)
        return band_of(store.read_frame(symbol, d, d), key)
    with httpx.Client(base_url=get_settings().alpaca_data_url.rstrip("/"), headers=_alpaca_headers(), timeout=10) as client:
        s, fills, todo = resolve(scored, lab, fills, lambda sym, key: news_flag(client, sym, key), band_fn)
    OUT.mkdir(parents=True, exist_ok=True)
    fills.assign(key=fills.key.map(lambda t: pd.Timestamp(t).isoformat())).to_csv(OUT / "fills.csv", index=False)
    print("early-alert forward: " + json.dumps(night(s, todo)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
