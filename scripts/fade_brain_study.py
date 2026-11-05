"""Fade brain v2 (prereg_fade_brain_2026-11-05): learn bet size from tape signals, news and SEC filings.

    python scripts/fade_brain_study.py dev     # monthly walk-forward on training events (run ONCE)
    python scripts/fade_brain_study.py freeze  # fit on all training events, save model + terciles, score held-out
    python scripts/fade_brain_study.py shadow  # nightly (runner): size every forward event with the frozen model

Events, nets, news and filing flags come from reports/fade_filings/*_events.csv (the champion's net, point-in-time
flags); v1 features and tape signals are recomputed from the SIP bar store.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import meta
from sgt.intraday.store import ParquetBarStore

PREREG = "docs/findings/prereg_fade_brain_2026-11-05.md"
SRC = {"dev": Path("reports/fade_filings/dev_events.csv"), "holdout": Path("reports/fade_filings/holdout_events.csv")}
OUT = Path("reports/fade_brain")
WF_START, MIN_MONTHS = "2025-07", 6
SEED, N_BOOT = 20261105, 10_000


def events(name: str, store: ParquetBarStore) -> pd.DataFrame:
    ev = pd.read_csv(SRC[name]).dropna(subset=["net"]).reset_index(drop=True)
    ev["posted_at"] = pd.to_datetime(ev.posted_at, utc=True)
    nf = meta.news_features(ev)                    # replaces the raw flag columns with typed, unknown-aware ones
    return ev.drop(columns=[c for c in nf if c in ev]).join(meta.build(ev, store)) \
        .join(meta.signal_features(ev, store)).join(nf)


def walk_forward(ev: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Out-of-sample prediction and size weight per event, monthly expanding."""
    months = pd.to_datetime(ev.session).dt.to_period("M")
    parts = []
    for m in sorted(months.unique()):
        if m < pd.Period(WF_START, "M"):
            continue
        tr, te = ev[months < m], ev[months == m]
        if tr.session.nunique() < 20 or months[months < m].nunique() < MIN_MONTHS or te.empty:
            continue
        model = meta.fit_cols(tr, tr.net.to_numpy(float), cols)
        ins, p = model.predict(tr[cols]), model.predict(te[cols])
        parts.append(pd.DataFrame({"pred": p, "w": meta.size_weight(p, ins)}, index=te.index))
    return ev.join(pd.concat(parts), how="inner")


def evaluate(wf: pd.DataFrame, rng) -> dict:
    eq = wf.net.groupby(wf.session).sum()
    wt = (wf.net * wf.w).groupby(wf.session).sum()
    diff = (wt - eq).to_numpy(float)
    lower = float(np.quantile(diff[rng.integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1), 0.05))
    terc = wf.groupby("w").net.agg(["size", "mean"]).rename(columns={"size": "n"})
    return {"events": len(wf), "sessions": int(eq.size), "ic": float(wf.pred.corr(wf.net, method="spearman")),
            "equal_per_session": float(eq.mean()), "weighted_per_session": float(wt.mean()),
            "diff_per_session": float(diff.mean()), "lower95": lower, "mean_weight": float(wf.w.mean()),
            "by_weight": {str(k): {"n": int(v.n), "mean": float(v["mean"])} for k, v in terc.iterrows()}}


def _print(name: str, r: dict) -> None:
    print(f"{name}: {r['events']} events, {r['sessions']} sessions, IC {r['ic']:+.3f}, mean weight {r['mean_weight']:.2f}")
    print(f"  net/session equal {r['equal_per_session']:+.3%} sized {r['weighted_per_session']:+.3%} "
          f"diff {r['diff_per_session']:+.3%} (lower95 {r['lower95']:+.3%})")
    print("  by weight: " + "  ".join(f"{k}x n{v['n']} {v['mean']:+.2%}" for k, v in r["by_weight"].items()))


def cmd_dev() -> int:
    store = ParquetBarStore("data/alpaca")
    ev = events("dev", store)
    rng = np.random.default_rng(SEED)
    v2 = evaluate(walk_forward(ev, meta.BRAIN_FEATURES), rng)
    v1 = evaluate(walk_forward(ev, meta.FEATURES), rng)
    ok = v2["lower95"] > 0 and 0.9 <= v2["mean_weight"] <= 1.1
    _print("DEV v2 (all signals + news + SEC) — decides", v2)
    _print("SECONDARY v1 features only, same sizing", v1)
    full = meta.fit_cols(ev, ev.net.to_numpy(float), meta.BRAIN_FEATURES)
    imp = sorted(zip(meta.BRAIN_FEATURES, full.feature_importances_.tolist()), key=lambda x: -x[1])[:12]
    print("  top features (gain splits): " + ", ".join(f"{k} {v}" for k, v in imp))
    verdict = "PASS" if ok else "FAIL"
    print(f"VERDICT (dev gate): {verdict}")
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"verdict": verdict, "v2": v2, "v1_features_secondary": v1, "importance": imp}
    (OUT / "dev.json").write_text(json.dumps(res, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-brain-2026-11-05-dev", kind="fade-brain-dev",
                                  params={"prereg": PREREG}, metrics=res)
    return 0


def cmd_freeze() -> int:
    store = ParquetBarStore("data/alpaca")
    ev = events("dev", store)
    model = meta.fit_cols(ev, ev.net.to_numpy(float), meta.BRAIN_FEATURES)
    ins = model.predict(ev[meta.BRAIN_FEATURES])
    meta.BRAIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(meta.BRAIN_PATH))
    lo, hi = np.quantile(ins, [1 / 3, 2 / 3])
    meta.BRAIN_PATH.with_suffix(".json").write_text(json.dumps({"lo": float(lo), "hi": float(hi),
                                                                "features": meta.BRAIN_FEATURES}, indent=2))
    sha = meta.sha256(meta.BRAIN_PATH)
    ho = events("holdout", store)
    p = model.predict(ho[meta.BRAIN_FEATURES])
    wf = ho.assign(pred=p, w=meta.size_weight(p, ins))
    sec = evaluate(wf, np.random.default_rng(SEED))
    _print("SECONDARY held-out 2026-07..10 (frozen model; already seen)", sec)
    print(f"frozen {meta.BRAIN_PATH} sha256 {sha[:16]}")
    res = {"sha256": sha, "holdout_secondary": sec}
    (OUT / "freeze.json").write_text(json.dumps(res, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-brain-v2-freeze", kind="fade-brain-freeze",
                                  params={"prereg": PREREG, "model": str(meta.BRAIN_PATH)}, metrics=res)
    return 0


def load_frozen():
    """The frozen v2 model and its terciles; refuses a model whose sha256 differs from the one frozen."""
    import lightgbm as lgb

    cfg = json.loads(meta.BRAIN_PATH.with_suffix(".json").read_text())
    if meta.sha256(meta.BRAIN_PATH) != cfg["sha256"] or cfg["features"] != meta.BRAIN_FEATURES:
        raise SystemExit("fade brain model does not match its frozen sha256/features: nothing scored")
    return lgb.Booster(model_file=str(meta.BRAIN_PATH)), cfg


def forward_news(ev: pd.DataFrame) -> pd.DataFrame:
    """News (Alpaca/Benzinga) and SEC flags at each forward alert, the same functions the live texts use.
    Unknown (API down, no SEC contact) stays NaN, as unknown filings were in training."""
    from fade_live import data_client, live_news
    from sgt.fast import filings, news

    rows = []
    with data_client() as c:
        for r in ev.itertuples():
            at = pd.Timestamp(r.posted_at).to_pydatetime()
            n = live_news(c, r.symbol, at)
            f = filings.lookup(r.symbol, at, since_8k=news.window_start(at))
            rows.append({"n_own": float(n["news_own"]) if n else np.nan, "n_earn": float(n["news_earn"]) if n else np.nan,
                         "f_8k": float(f["eightk_today"]) if f else np.nan, "f_dil": float(f["dilution_30d"]) if f else np.nan})
    return pd.DataFrame(rows, index=ev.index)


def summary_line(t: pd.DataFrame) -> str:
    by = "  ".join(f"{w}x n{len(g)} {g.net.mean():+.1%}" for w, g in t.groupby("w"))
    return (f"fade brain fwd n={len(t)}: equal {t.net.mean():+.2%}/trade, sized {(t.net * t.w).mean():+.2%}/trade "
            f"(mean size {t.w.mean():.2f}x); {by}")


def cmd_shadow() -> int:
    from datetime import datetime

    from alert_fade_forward import CHANNEL, DB, forward_events
    from alert_fade_test import score
    from fade_challengers import ET
    from megacap_forward import last_complete_session
    from telegram_alert_test import load_alerts

    model, cfg = load_frozen()
    store = ParquetBarStore("data/alpaca")
    end = last_complete_session(datetime.now(ET))
    ev = forward_events(load_alerts(DB, CHANNEL), store, end, fill_bars=False).reset_index(drop=True)
    ev = ev[ev.status == "ok"].reset_index(drop=True) if "status" in ev else ev
    if ev.empty:
        print("no forward events yet")
        return 0
    ev["net"] = score(ev, store).net.to_numpy()
    ev = ev.dropna(subset=["net"]).reset_index(drop=True)
    ev["posted_at"] = pd.to_datetime(ev.posted_at, utc=True)
    x = ev.join(meta.build(ev, store)).join(meta.signal_features(ev, store)).join(forward_news(ev))
    p = model.predict(x[meta.BRAIN_FEATURES])
    t = ev[["symbol", "session", "posted_at", "net"]].assign(
        pred=p, w=np.where(p < cfg["lo"], meta.WEIGHTS[0], np.where(p < cfg["hi"], meta.WEIGHTS[1], meta.WEIGHTS[2])))
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "shadow.csv", index=False)
    line = summary_line(t)
    (OUT / "shadow_summary.json").write_text(json.dumps({"asof": str(end), "n": len(t), "line": line}, indent=2))
    print(line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "freeze", "shadow"])
    return {"dev": cmd_dev, "freeze": cmd_freeze, "shadow": cmd_shadow}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
