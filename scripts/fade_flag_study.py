"""The bull flag as a risk control for the short fade (prereg_fade_flag_2026-11-10).

    python scripts/fade_flag_study.py      # run ONCE: X1 decided on held-out, X2 on training + held-out confirm
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alert_fade_test import LOCATE, STOP, cover  # noqa: E402
from long_family_study import Bars  # noqa: E402

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402

OUT = Path("reports/fade_flag")
SEED, N_BOOT, ALPHA, D_STAR = 20261111, 10_000, 0.025, "2026-07-01"
HOLD = pd.Timedelta(minutes=60)
PRE = pd.Timedelta(minutes=15)


def cover_with_flag(w: pd.DataFrame, entry: float, flag: dict | None) -> tuple[float | None, bool]:
    """(cover price, fired): the fade's cover() plus a buy-stop at the flag high from the flag's ready time."""
    if flag is None or w.empty:
        return cover(w, entry), False
    lvl_stop = entry * (1 + STOP)
    lvl_flag = flag["flag_hi"]
    for b in w.itertuples():
        armed = b.ts >= flag["ready"]
        hit_flag = armed and b.high >= lvl_flag and lvl_flag < lvl_stop
        if hit_flag:
            return float(max(lvl_flag, b.open)), True
        if b.high >= lvl_stop:
            return float(max(lvl_stop, b.open)), False
    return float(w.close.iloc[-1]), False


def events() -> pd.DataFrame:
    n = pd.concat([pd.read_csv(f"reports/fade_news/{s}_events.csv") for s in ("dev", "holdout")])
    n = n.assign(session=n.session.astype(str))[["symbol", "session", "net", "news_any"]]
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")
    src = src.assign(session=src.session.astype(str))[["symbol", "session", "entry_ts", "entry", "cost_rt"]]
    e = n.merge(src, on=["symbol", "session"]).dropna(subset=["entry_ts"])
    return e.assign(t0=pd.to_datetime(e.entry_ts, utc=True)).sort_values(["symbol", "session"]).reset_index(drop=True)


def boot(d: pd.DataFrame, col: str) -> np.ndarray:
    u, inv = np.unique(d.session.to_numpy(), return_inverse=True)
    s, n = np.bincount(inv, d[col].to_numpy(float), len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    return s[b].sum(1) / n[b].sum(1)


def main() -> int:
    bars, rows = Bars(), []
    for r in events().itertuples():
        f = bars(r.symbol, r.session)
        w = f[(f.ts >= r.t0) & (f.ts < r.t0 + HOLD)]
        plain = cover(w, r.entry)
        flag = pt.find_flag(f, r.t0)
        c1, fired = cover_with_flag(w, r.entry, flag)
        pre = pt.find_flag(f[f.ts < r.t0], r.t0 - PRE)
        fee = r.cost_rt + LOCATE
        g = (lambda px: None if px is None else (r.entry - px) / r.entry - fee)
        rows.append({"symbol": r.symbol, "session": r.session, "news_any": r.news_any, "stored": r.net,
                     "plain": g(plain), "x1": g(c1), "x1_fired": fired, "x2_skip": pre is not None})
    t = pd.DataFrame(rows)
    t["split"] = np.where(t.session < D_STAR, "train", "holdout")
    t["delta"] = t.x1 - t.plain
    gap = (t.plain - t.stored).abs().max()
    print(f"baseline check: max |plain - stored| = {gap:.2e} over {len(t)} events")
    if not gap < 1e-9:
        print("baseline does not reproduce the stored fade nets: STOP (prereg)")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "events.csv", index=False)
    out = {}
    for split in ("train", "holdout"):
        d = t[t.split == split].dropna(subset=["plain", "x1"])
        m = boot(d, "delta")
        nn = d[d.news_any == False]  # noqa: E712
        out[f"x1_{split}"] = {"n": len(d), "fired": int(d.x1_fired.sum()), "mean_delta": float(d.delta.mean()),
                              "p": float((m <= 0).mean()), "plain": float(d.plain.mean()), "x1": float(d.x1.mean()),
                              "nonews_delta": float(nn.delta.mean()), "nonews_n": len(nn),
                              "fired_plain": float(d[d.x1_fired].plain.mean()) if d.x1_fired.any() else None,
                              "fired_x1": float(d[d.x1_fired].x1.mean()) if d.x1_fired.any() else None}
        sk = d[d.x2_skip]
        out[f"x2_{split}"] = {"skipped": len(sk), "skipped_mean": float(sk.plain.mean()) if len(sk) else None,
                              "p_lt0": float((boot(sk, "plain") >= 0).mean()) if len(sk) else None,
                              "kept_mean": float(d[~d.x2_skip].plain.mean())}
    h, tr = out["x1_holdout"], out["x2_train"]
    out["X1_pass"] = bool(h["mean_delta"] > 0 and h["p"] < ALPHA and h["fired"] >= 30 and h["nonews_delta"] >= 0)
    out["X2_pass"] = bool(tr["skipped"] >= 50 and tr["skipped_mean"] is not None and tr["skipped_mean"] < 0
                          and tr["p_lt0"] < ALPHA)
    ho = out["x2_holdout"]
    out["X2_confirmed"] = bool(out["X2_pass"] and ho["skipped"] >= 15 and (ho["skipped_mean"] or 0) < 0)
    print(json.dumps(out, indent=1))
    (OUT / "result.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-flag-2026-11-10", kind="fade-flag",
                                  params={"prereg": "docs/findings/prereg_fade_flag_2026-11-10.md"}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
