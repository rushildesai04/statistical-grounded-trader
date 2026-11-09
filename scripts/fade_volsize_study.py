"""Size each fade by pre-alert 1-minute volatility (prereg_fade_volsize_2026-11-08) and the forward shadows that
followed from its result (prereg_fade_calmskip_2026-11-08).

    python scripts/fade_volsize_study.py           # run once: training gate + held-out secondary (done 2026-11-08)
    python scripts/fade_volsize_study.py shadow    # nightly on the runner: calm skip + vol up on forward events
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/fade_volsize")
CALM, M = 0.015072, 0.025540                    # training 20th percentile / median sigma (prereg_fade_calmskip)
SHADOWS = {"calmskip": Path("reports/fade_calmskip"), "volup": Path("reports/fade_volup"),
           "evsize": Path("reports/fade_evsize")}                     # prereg_fade_evsize_2026-11-09
SEED, N_BOOT, ALPHA, LOOKBACK, MIN_BARS, LO, HI = 20261108, 10_000, 0.05, 30, 10, 0.5, 2.0


def sigma(store: ParquetBarStore, symbol: str, session: str, entry_ts: pd.Timestamp) -> float | None:
    d = date.fromisoformat(session)
    f = store.read_frame(symbol, d, d)
    if f.empty:
        return None
    ts = pd.to_datetime(f.ts, utc=True)
    done = f.loc[ts + pd.Timedelta(minutes=1) <= entry_ts].assign(ts=ts).sort_values("ts").tail(LOOKBACK + 1)
    c = done.close.to_numpy(float)
    c = c[c > 0]
    if len(c) < MIN_BARS + 1:
        return None
    s = float(np.std(np.diff(np.log(c)), ddof=1))
    return s if math.isfinite(s) and s > 0 else None


def events(name: str, store: ParquetBarStore) -> pd.DataFrame:
    e = pd.read_csv(f"reports/fade_news/{name}_events.csv")
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "entry_ts", "entry"]]
    e = e.merge(src, on=["symbol", "session"], how="left").dropna(subset=["net", "entry_ts"])
    e["sigma"] = [sigma(store, r.symbol, str(r.session), pd.Timestamp(r.entry_ts)) for r in e.itertuples()]
    return e


def evaluate(e: pd.DataFrame, m: float) -> dict:
    w = np.where(e.sigma.notna(), np.clip(m / e.sigma.fillna(m), LO, HI), 1.0)
    net = e.net.to_numpy(float)
    u, inv = np.unique(e.session.astype(str).to_numpy(), return_inverse=True)
    swn, sw, sn, cnt = (np.bincount(inv, x, len(u)) for x in (w * net, w, net, np.ones_like(net)))
    rng = np.random.default_rng(SEED)
    b = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    diff = swn[b].sum(1) / sw[b].sum(1) - sn[b].sum(1) / cnt[b].sum(1)
    scale = cnt.sum() / sw.sum()                           # same average dollars as equal weight

    def sharpe(x):
        return float(x.mean() / x.std() * math.sqrt(252)) if x.std() > 0 else None
    q = pd.qcut(e.sigma, 5, labels=False, duplicates="drop")
    res = {"n": len(e), "coverage": float(e.sigma.notna().mean()), "mean_w": float(w.mean()),
           "equal": float(net.mean()), "weighted_per_dollar": float((w * net).sum() / w.sum()),
           "diff": float(diff.mean()), "diff_lower": float(np.quantile(diff, ALPHA)),
           "sharpe_equal": sharpe(sn), "sharpe_weighted": sharpe(swn * scale),
           "by_quintile": {int(k): float(v) for k, v in e.groupby(q).net.mean().items()},
           "corr_sigma_price": float(e[["sigma", "entry"]].corr(method="spearman").iloc[0, 1])}
    res["pass"] = bool(res["diff_lower"] > 0)
    return res


def shadow_weights(sigma: pd.Series) -> dict[str, np.ndarray]:
    """Unknown sigma -> 1.0 (the live rule) in both challengers."""
    sg = sigma.astype(float).to_numpy()
    known = np.isfinite(sg) & (sg > 0)
    calm = np.where(known & (sg < CALM), 0.0, 1.0)
    up = np.where(known, np.clip(np.where(known, sg, M) / M, LO, HI), 1.0)
    return {"calmskip": calm, "volup": up}


def ev_weights(t: pd.DataFrame) -> np.ndarray:
    """EV sizing (prereg_fade_evsize_2026-11-09) with the frozen config/models/fade_ev_v1.json. The forward record has no
    company-news flag, so news-TYPE alerts get weight 1 (the live book filters them; they stay out of the comparison)."""
    import json as _json

    from fade_ev_study import MODEL, groups, predict

    m = _json.loads(MODEL.read_text())
    g = groups(t.entry_ts, t.entry, t.sigma)
    w = np.clip(predict(m, g) / m["mean_ev"], m["lo"], m["hi"]) * m["scale"]
    news = (t.get("alert_type", pd.Series("", index=t.index)) == "news").to_numpy()
    return np.where(news, 1.0, w)


def summary_line(name: str, t: pd.DataFrame) -> str:
    if name == "calmskip":
        calm = t.w == 0
        return (f"fade calm skip fwd n={len(t)}: live {t.net.mean():+.2%}/trade; skipped {int(calm.sum())} calm "
                f"({t.net[calm].mean() if calm.any() else 0:+.1%} avg), kept {t.net[~calm].mean() if (~calm).any() else 0:+.2%}")
    label = "EV" if name == "evsize" else "vol-up"
    return f"fade {label} sizing fwd n={len(t)}: equal {t.net.mean():+.2%}/trade, sized {(t.net * t.w).mean():+.2%}/trade"


def cmd_shadow() -> int:
    import sys
    from datetime import datetime

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from alert_fade_forward import CHANNEL, DB, forward_events
    from alert_fade_test import score
    from fade_challengers import ET
    from megacap_forward import last_complete_session
    from telegram_alert_test import load_alerts

    store = ParquetBarStore("data/alpaca")
    end = last_complete_session(datetime.now(ET))
    ev = forward_events(load_alerts(DB, CHANNEL), store, end, fill_bars=False).reset_index(drop=True)
    ev = ev[ev.status == "ok"].reset_index(drop=True) if "status" in ev else ev
    if ev.empty:
        print("no forward events yet")
        return 0
    ev["net"] = score(ev, store).net.to_numpy()
    keep = [c for c in ("symbol", "session", "alert_type", "entry_ts", "entry", "net") if c in ev]
    t = ev.dropna(subset=["net"])[keep].reset_index(drop=True)
    t["sigma"] = [sigma(store, r.symbol, str(r.session), pd.Timestamp(r.entry_ts)) for r in t.itertuples()]
    all_w = shadow_weights(t.sigma)
    all_w["evsize"] = ev_weights(t)
    for name, w in all_w.items():
        out = t.assign(w=w)
        SHADOWS[name].mkdir(parents=True, exist_ok=True)
        out.to_csv(SHADOWS[name] / "shadow.csv", index=False)
        line = summary_line(name, out)
        (SHADOWS[name] / "shadow_summary.json").write_text(json.dumps({"asof": str(end), "n": len(out), "line": line},
                                                                      indent=2))
        print(line)
    return 0


def cmd_dev() -> int:
    store = ParquetBarStore("data/alpaca")
    dev = events("dev", store)
    m = float(dev.sigma.median())
    out = {"m": m, "dev": evaluate(dev, m), "holdout": evaluate(events("holdout", store), m)}
    for k in ("dev", "holdout"):
        r = out[k]
        print(f"{k:7s} n{r['n']} cov {r['coverage']:.0%} equal {r['equal']:+.2%} weighted/$ {r['weighted_per_dollar']:+.2%} "
              f"diff {r['diff']:+.2%} (lower {r['diff_lower']:+.2%}) Sharpe {r['sharpe_equal']:.2f} -> "
              f"{r['sharpe_weighted']:.2f} -> {'PASS' if r['pass'] else 'fail'}{' (decides)' if k == 'dev' else ''}")
        print(f"        by sigma quintile (low->high) {({q: round(v * 100, 2) for q, v in r['by_quintile'].items()})} "
              f"corr(sigma, price) {r['corr_sigma_price']:+.2f}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-volsize-2026-11-08-dev", kind="fade-volsize-dev",
                                  params={"prereg": "docs/findings/prereg_fade_volsize_2026-11-08.md"}, metrics=out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="dev", choices=["dev", "shadow"])
    return {"dev": cmd_dev, "shadow": cmd_shadow}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
