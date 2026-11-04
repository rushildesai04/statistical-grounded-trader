"""Learned fade filter v1 (prereg_fade_meta_2026-11-04): develop, freeze, score the forward shadow book.

    python scripts/fade_meta_study.py dev      # walk-forward on training events (2025-01..2026-06), gate
    python scripts/fade_meta_study.py freeze   # only after a dev PASS: fit on all training, save model, held-out report
    python scripts/fade_meta_study.py shadow   # nightly (runner): frozen model on forward events vs take-all
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alert_fade_test import score
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import meta
from sgt.intraday.store import ParquetBarStore

ET = ZoneInfo("America/New_York")
SRC = Path("reports/telegram/trades_pooled_first.csv")
OUT = Path("reports/fade_meta")
WF_START, MIN_MONTHS = "2025-07", 6
SEED, N_BOOT = 20261104, 10_000
THRESH = meta.MODEL_PATH.with_suffix(".json")


def events(split: str, store: ParquetBarStore) -> pd.DataFrame:
    ev = pd.read_csv(SRC)
    ev = ev[(ev.split == split) & (ev.status == "ok")].reset_index(drop=True)
    t = score(ev, store)
    ev["net"] = t.net.to_numpy()
    ev = ev.dropna(subset=["net"]).reset_index(drop=True)
    return ev.join(meta.build(ev, store))


def per_session(ev: pd.DataFrame, take: np.ndarray) -> pd.Series:
    return (ev.net * take).groupby(ev.session).sum()


def paired_lower(diff: np.ndarray, rng) -> float:
    idx = rng.integers(0, len(diff), size=(N_BOOT, len(diff)))
    return float(np.quantile(diff[idx].mean(axis=1), 0.05))


def cmd_dev() -> int:
    store = ParquetBarStore("data/alpaca")
    ev = events("train", store)
    months = pd.to_datetime(ev.session).dt.to_period("M")
    preds, takes = [], {q: [] for q in meta.QS}
    for m in sorted(months.unique()):
        if m < pd.Period(WF_START, "M"):
            continue
        tr, te = ev[months < m], ev[months == m]
        if tr.session.nunique() < 20 or months[months < m].nunique() < MIN_MONTHS or te.empty:
            continue
        model = meta.fit(tr, tr.net.to_numpy(float))
        ins, p = model.predict(tr[meta.FEATURES]), model.predict(te[meta.FEATURES])
        preds.append(pd.Series(p, index=te.index))
        for q in meta.QS:
            takes[q].append(pd.Series(meta.take(p, ins, q), index=te.index))
    pred = pd.concat(preds)
    wf = ev.loc[pred.index].assign(pred=pred)
    base = per_session(wf, np.ones(len(wf)))
    table = {"0.00": {"n": len(wf), "per_session": float(base.mean()), "mean": float(wf.net.mean()),
                      "win": float((wf.net > 0).mean()), "worst": float(wf.net.min())}}
    rng = np.random.default_rng(SEED)
    for q in meta.QS:
        tk = pd.concat(takes[q]).reindex(wf.index).to_numpy(bool)
        s = per_session(wf, tk).reindex(base.index, fill_value=0.0)
        sub = wf[tk]
        table[f"{q:.2f}"] = {"n": int(tk.sum()), "per_session": float(s.mean()), "mean": float(sub.net.mean()),
                             "win": float((sub.net > 0).mean()), "worst": float(sub.net.min()),
                             "lower95_vs_all": paired_lower((s - base).to_numpy(float), rng)}
    ic = float(wf.pred.corr(wf.net, method="spearman"))
    best = max(meta.QS, key=lambda q: table[f"{q:.2f}"]["per_session"])
    ok = table[f"{best:.2f}"]["per_session"] > table["0.00"]["per_session"] and \
        table[f"{best:.2f}"]["lower95_vs_all"] > 0
    print(f"DEV walk-forward {wf.session.min()} .. {wf.session.max()}: {len(wf)} events, {base.size} sessions, "
          f"rank IC {ic:+.3f}")
    for k, v in table.items():
        print(f"  q={k}: n {v['n']:5d} net/session {v['per_session']:+.3%} mean/trade {v['mean']:+.2%} "
              f"win {v['win']:.0%} worst {v['worst']:+.1%}" +
              (f"  lower95 vs take-all {v['lower95_vs_all']:+.3%}" if "lower95_vs_all" in v else ""))
    verdict = "PASS" if ok else "FAIL"
    print(f"SELECTED q={best:.2f}  VERDICT (dev gate): {verdict}")
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"verdict": verdict, "q": best, "ic": ic, "table": table}
    (OUT / "dev.json").write_text(json.dumps(res, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-meta-2026-11-04-dev", kind="fade-meta-dev",
                                  params={"prereg": "docs/findings/prereg_fade_meta_2026-11-04.md"}, metrics=res)
    return 0


def cmd_freeze() -> int:
    dev = json.loads((OUT / "dev.json").read_text())
    if dev["verdict"] != "PASS":
        raise SystemExit("dev gate FAILED: the prereg says nothing is deployed")
    store = ParquetBarStore("data/alpaca")
    tr = events("train", store)
    model = meta.fit(tr, tr.net.to_numpy(float))
    meta.MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(meta.MODEL_PATH))
    ins = model.predict(tr[meta.FEATURES])
    THRESH.write_text(json.dumps({"q": dev["q"], "threshold": float(np.quantile(ins, dev["q"])),
                                  "trained_on": [str(tr.session.min()), str(tr.session.max())], "n": len(tr)}, indent=2))
    sha = meta.sha256(meta.MODEL_PATH)
    print(f"frozen {meta.MODEL_PATH} sha256 {sha}")
    ho = events("holdout", store)                      # secondary: already examined once for the base rule
    p = model.predict(ho[meta.FEATURES])
    keep = p >= np.quantile(ins, dev["q"])
    print(f"SECONDARY held-out 2026-07..10: take-all mean {ho.net.mean():+.2%} (n {len(ho)}) | filtered "
          f"mean {ho.net[keep].mean():+.2%} (n {int(keep.sum())}) | net/session all "
          f"{per_session(ho, np.ones(len(ho))).mean():+.3%} vs filtered {per_session(ho, keep).mean():+.3%}")
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-meta-v1-forward", kind="fade-meta-freeze",
                                  params={"model_sha256": sha, "q": dev["q"]},
                                  metrics={"holdout_all": float(ho.net.mean()), "holdout_kept": float(ho.net[keep].mean()),
                                           "holdout_n_kept": int(keep.sum())})
    return 0


def cmd_shadow() -> int:
    import lightgbm as lgb

    from alert_fade_forward import CHANNEL, DB, forward_events
    from megacap_forward import last_complete_session
    from telegram_alert_test import load_alerts

    if not meta.MODEL_PATH.exists():
        print("no frozen fade filter (dev gate not passed); nothing to shadow")
        return 0
    th = json.loads(THRESH.read_text())
    model = lgb.Booster(model_file=str(meta.MODEL_PATH))
    store = ParquetBarStore("data/alpaca")
    end = last_complete_session(datetime.now(ET))
    ev = forward_events(load_alerts(DB, CHANNEL), store, end, fill_bars=False).reset_index(drop=True)
    t = score(ev, store)
    ev = ev.assign(net=t.net.to_numpy()).dropna(subset=["net"]).reset_index(drop=True)
    if ev.empty:
        print("no forward events yet")
        return 0
    ev = ev.join(meta.build(ev, store))
    ev["pred"] = model.predict(ev[meta.FEATURES])
    ev["take"] = ev.pred >= th["threshold"]
    OUT.mkdir(parents=True, exist_ok=True)
    ev[["symbol", "session", "alert_type", "net", "pred", "take"]].to_csv(OUT / "shadow.csv", index=False)
    a, f = per_session(ev, np.ones(len(ev))), per_session(ev, ev.take.to_numpy())
    print(f"shadow v1 (q={th['q']:.2f}): {len(ev)} forward events, kept {int(ev.take.sum())}; net/session "
          f"take-all {a.mean():+.3%} vs filter {f.mean():+.3%}; mean/trade all {ev.net.mean():+.2%} kept "
          f"{ev.net[ev.take].mean():+.2%}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "freeze", "shadow"])
    return {"dev": cmd_dev, "freeze": cmd_freeze, "shadow": cmd_shadow}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
