"""SEC filings at alert time vs the fade's result (prereg_fade_filings_2026-11-05).

    python scripts/fade_filings_study.py fetch   # EDGAR submissions for every event symbol (cached, resumable)
    python scripts/fade_filings_study.py dev     # gates on development events (run ONCE); held-out secondary

Events and nets come from the news study's event files (same champion nets, same posted_at).
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.core.secrets import get_secret
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import filings, news

PREREG = "docs/findings/prereg_fade_filings_2026-11-05.md"
EVENTS = {"dev": Path("reports/fade_news/dev_events.csv"), "holdout": Path("reports/fade_news/holdout_events.csv")}
CACHE = Path("data/edgar")
OUT = Path("reports/fade_filings")
SEED, N_BOOT, ALPHA, MIN_N = 20261105, 10_000, 0.05 / 2, 100
log = logging.getLogger("fade_filings")


def _client() -> httpx.Client:
    contact = get_secret("sec_contact")
    return httpx.Client(headers={"User-Agent": f"statistical-grounded-trader {contact}"}, timeout=10)


def _merged(cik: int, client: httpx.Client) -> dict | None:
    """Submissions with the older pages merged into ``recent`` (form + acceptanceDateTime only)."""
    path = CACHE / f"CIK{cik:010d}.json"
    if path.exists():
        return json.loads(path.read_text())
    sub = filings._get_json(client, filings.SUBMISSIONS_URL.format(cik=cik))
    time.sleep(0.15)
    if sub is None:
        return None
    rec = (sub.get("filings") or {}).get("recent") or {}
    forms, acc = list(rec.get("form") or []), list(rec.get("acceptanceDateTime") or [])
    for f in (sub.get("filings") or {}).get("files") or []:
        if not isinstance(f, dict) or str(f.get("filingTo", "")) < "2024-11-01":
            continue                                         # pages older than the first event's lookback
        page = filings._get_json(client, f"https://data.sec.gov/submissions/{f.get('name')}")
        time.sleep(0.15)
        if page:
            forms += list(page.get("form") or [])
            acc += list(page.get("acceptanceDateTime") or [])
    out = {"filings": {"recent": {"form": forms, "acceptanceDateTime": acc}}}
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def cmd_fetch() -> int:
    syms = sorted(set().union(*(set(pd.read_csv(p).symbol) for p in EVENTS.values())))
    with _client() as c:
        tick = filings._tickers(c, datetime.now(timezone.utc))
        got = miss = 0
        for i, s in enumerate(syms, 1):
            cik = tick.get(str(s).upper().replace(".", "-"))
            if cik is None:
                miss += 1
                continue
            got += _merged(cik, c) is not None
            if i % 200 == 0:
                log.info("%d/%d symbols (%d with filings, %d without a CIK)", i, len(syms), got, miss)
    print(f"{len(syms)} symbols: {got} fetched, {miss} without a CIK today")
    return 0


def flag_events(ev: pd.DataFrame) -> pd.DataFrame:
    tick = json.loads(filings.TICKER_CACHE.read_text())
    rows = []
    for r in ev.itertuples(index=False):
        at = pd.Timestamp(r.posted_at).to_pydatetime()
        cik = tick.get(str(r.symbol).upper().replace(".", "-"))
        path = CACHE / f"CIK{cik:010d}.json" if cik else None
        if not path or not path.exists():
            rows.append({"known": 0})
            continue
        sub = json.loads(path.read_text())
        at_strict = at - pd.Timedelta(microseconds=1).to_pytimedelta()   # accepted strictly before the alert
        f = filings.flags(sub, at_strict, since_8k=news.window_start(at))
        forms = f["forms_30d"] or ""
        rec = [(filings._accepted(a), fm) for fm, a in zip(sub["filings"]["recent"]["form"],
                                                            sub["filings"]["recent"]["acceptanceDateTime"])]
        f8k = any(fm in ("8-K", "6-K", "8-K/A", "6-K/A") and t and news.window_start(at) < t < at for t, fm in rec)
        any24 = any(t and at - pd.Timedelta(hours=24) < t < at for t, _ in rec)
        per3 = any(fm in ("10-Q", "10-K", "20-F", "40-F") and t and at - pd.Timedelta(days=3) < t < at for t, fm in rec)
        rows.append({"known": 1, "f_8k": int(f8k), "f_dil": f["dilution_30d"], "any_24h": int(any24),
                     "periodic_3d": int(per3), "forms_30d": forms})
    return pd.concat([ev.reset_index(drop=True), pd.DataFrame(rows)], axis=1)


def _boot_diff(t: pd.DataFrame, flag: str, rng) -> np.ndarray:
    """Bootstrap over sessions of mean(net | flag=1) - mean(net | flag=0)."""
    g = t.groupby("session")
    s1 = g.apply(lambda x: x.net[x[flag] == 1].sum(), include_groups=False).to_numpy(float)
    n1 = g.apply(lambda x: (x[flag] == 1).sum(), include_groups=False).to_numpy(float)
    s0 = g.apply(lambda x: x.net[x[flag] == 0].sum(), include_groups=False).to_numpy(float)
    n0 = g.apply(lambda x: (x[flag] == 0).sum(), include_groups=False).to_numpy(float)
    b = rng.integers(0, len(s1), size=(N_BOOT, len(s1)))
    with np.errstate(invalid="ignore", divide="ignore"):
        return s1[b].sum(1) / n1[b].sum(1) - s0[b].sum(1) / n0[b].sum(1)


def _skip_gain(t: pd.DataFrame, flag: str, rng) -> np.ndarray:
    """Bootstrap of mean per-session (skip flagged - take all) net = -(flagged net) per session."""
    d = -t[t[flag] == 1].groupby("session").net.sum().reindex(t.session.unique(), fill_value=0).to_numpy(float)
    return d[rng.integers(0, len(d), size=(N_BOOT, len(d)))].mean(1)


def table(t: pd.DataFrame, flag: str) -> dict:
    a, b = t[t[flag] == 1].net, t[t[flag] == 0].net
    return {"n1": len(a), "mean1": float(a.mean()) if len(a) else None, "win1": float((a > 0).mean()) if len(a) else None,
            "n0": len(b), "mean0": float(b.mean()) if len(b) else None, "win0": float((b > 0).mean()) if len(b) else None}


def evaluate(t: pd.DataFrame) -> dict:
    k = t[t.known == 1].copy()
    rng = np.random.default_rng(SEED)
    res = {"coverage": float(t.known.mean()), "n_known": len(k), "unknown_mean": float(t[t.known == 0].net.mean())}
    d8 = _boot_diff(k, "f_8k", rng)
    sk = _skip_gain(k, "f_8k", rng)
    h1 = {**table(k, "f_8k"), "diff": float(np.nanmean(d8)), "diff_upper": float(np.nanquantile(d8, 1 - ALPHA)),
          "skip_gain_per_session": float(sk.mean()), "skip_lower": float(np.quantile(sk, ALPHA))}
    h1["pass"] = bool(h1["n1"] >= MIN_N and h1["diff_upper"] < 0 and h1["skip_lower"] > 0)
    dd = _boot_diff(k, "f_dil", rng)
    h2 = {**table(k, "f_dil"), "diff": float(np.nanmean(dd)), "diff_lower": float(np.nanquantile(dd, ALPHA))}
    h2["pass"] = bool(h2["n1"] >= MIN_N and h2["diff_lower"] > 0)
    res.update(h1=h1, h2=h2)
    k["both"] = ((k.f_8k == 1) & (k.f_dil == 1)).astype(int)
    res["secondary"] = {f: table(k, f) for f in ("any_24h", "periodic_3d", "both")}
    k["news_own"] = k.news_own.astype(str).str.lower().isin(["true", "1"]).astype(int)
    res["secondary"]["news_x_8k"] = {f"news{a}_8k{b}": {"n": int(((k.news_own == a) & (k.f_8k == b)).sum()),
                                                        "mean": float(k.net[(k.news_own == a) & (k.f_8k == b)].mean())}
                                     for a in (0, 1) for b in (0, 1)}
    return res


def _print(name: str, r: dict) -> None:
    h1, h2 = r["h1"], r["h2"]
    print(f"{name}: coverage {r['coverage']:.0%} (unknown mean {r['unknown_mean']:+.2%})")
    print(f"  H1 f_8k: n {h1['n1']} mean {h1['mean1']:+.2%} win {h1['win1']:.0%} vs rest {h1['mean0']:+.2%} "
          f"diff {h1['diff']:+.2%} (upper {h1['diff_upper']:+.2%}); skip gain/session {h1['skip_gain_per_session']:+.3%} "
          f"(lower {h1['skip_lower']:+.3%}) -> {'PASS' if h1['pass'] else 'fail'}")
    print(f"  H2 f_dil: n {h2['n1']} mean {h2['mean1']:+.2%} win {h2['win1']:.0%} vs rest {h2['mean0']:+.2%} "
          f"diff {h2['diff']:+.2%} (lower {h2['diff_lower']:+.2%}) -> {'PASS' if h2['pass'] else 'fail'}")
    for k, v in r["secondary"].items():
        print(f"  {k}: {v}")


def cmd_dev() -> int:
    out = {}
    for name, p in EVENTS.items():
        t = flag_events(pd.read_csv(p))
        OUT.mkdir(parents=True, exist_ok=True)
        t.to_csv(OUT / f"{name}_events.csv", index=False)
        out[name] = evaluate(t)
        _print("DEV (decides)" if name == "dev" else "HELD-OUT (secondary, already seen)", out[name])
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-filings-2026-11-05-dev", kind="fade-filings-dev",
                                  params={"prereg": PREREG}, metrics=json.loads(json.dumps(out, default=str)))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "dev"])
    return {"fetch": cmd_fetch, "dev": cmd_dev}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
