"""Risk lessons from the 2026-11-12 losing longs (owner notes; docs/findings/prereg_long_risk_2026-11-12.md).

    nice -n 10 .venv/bin/python scripts/long_risk_study.py      # run ONCE (frozen prereg)

The live long book on past alerts (first long setup to fill per event, the tested simulator) with four candidate
changes: W skip wide stops, R size by risk, G skip fills that opened more than 1R above the buy-stop trigger, E sell at
the next open when the fill candle closes below the entry. Report only; nothing here changes a live rule.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_family_study import Bars, fade_events, halt_events  # noqa: E402
from long_runner_study import first_fill  # noqa: E402
from long_setups_study import D_STAR, latest  # noqa: E402
from trader_rules_long_study import _rng_idx, bar_index, boot_groups, boot_mean  # noqa: E402

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402

OUT = Path("reports/long_risk")
ALPHA = 0.05 / 4                                     # Bonferroni over the four candidates
MIN_TRAIN, MIN_HOLD = 100, 30
Q_SKIP, Q_RISK, GAP_MAX = 0.80, 0.50, 1.0            # W: 80th pct of d; R: median d; G: fill > 1R above trigger


def _p(d: np.ndarray) -> float:
    """One-sided p: share of resampled statistics <= 0; an undefined resample (a group empty in it) counts against
    the candidate (fail closed)."""
    d = np.asarray(d, float)
    return float(((d <= 0) | np.isnan(d)).mean())


def risk_fields(s: pt.Setup, t: pt.Trade) -> dict:
    """Stop distance d from the fill, and for buy-stop setups the gap g in planned R above the trigger."""
    d = (t.entry - s.stop) / t.entry
    g = 0.0 if s.trigger is None else (t.entry - s.trigger) / (s.trigger - s.stop)
    return {"entry": t.entry, "stop": s.stop, "trigger": s.trigger, "d": d, "g": g}


def red_exit(f: pd.DataFrame, t: pt.Trade, fill_i: int) -> tuple[float, bool]:
    """E: gross of the trade under the red-fill-candle exit, and whether the rule changed it."""
    if t.exit_ts == t.entry_ts:                      # closed on its fill bar (stop or target): unchanged
        return t.gross, False
    close = float(f.close.iloc[fill_i])
    if not close < t.entry:
        return t.gross, False
    px = float(f.open.iloc[fill_i + 1]) if fill_i + 1 < len(f) else close
    return px / t.entry - 1, True


def event_rows(ev: pd.DataFrame, bars: Bars, halt: bool) -> list[dict]:
    rows = []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        hit = first_fill(f, pd.Timestamp(r.t0), halt, latest(r.session))
        if hit is None:
            continue
        s, t = hit
        cost = float(r.cost_rt)
        e_gross, changed = red_exit(f, t, bar_index(f, t.entry_ts))
        rows.append({"symbol": r.symbol, "session": r.session, "halt": halt, "kind": s.kind, "entry_ts": t.entry_ts,
                     "reason": t.reason, "net": t.gross - cost, "e_net": e_gross - cost, "e_changed": changed,
                     **risk_fields(s, t)})
    return rows


def skip_stats(b: pd.DataFrame, skip: pd.Series) -> dict:
    """Kept minus skipped mean net (sessions resampled jointly)."""
    kept, sk = b[~skip], b[skip]
    out = {"n_kept": len(kept), "n_skipped": len(sk), "base": float(b.net.mean()),
           "kept": float(kept.net.mean()) if len(kept) else None, "skipped": float(sk.net.mean()) if len(sk) else None}
    if len(kept) and len(sk):
        d = boot_groups(kept.session.to_numpy(), kept.net.to_numpy(float), sk.session.to_numpy(), sk.net.to_numpy(float))
        out.update(diff=out["kept"] - out["skipped"], p=_p(d),
                   kept_lower95=float(np.quantile(boot_mean(kept.session.to_numpy(), kept.net.to_numpy(float)), 0.05)))
    return out


def w_stats(b: pd.DataFrame, d80: float) -> dict:
    return {"d80": d80, **skip_stats(b, b.d > d80)}


def g_stats(b: pd.DataFrame) -> dict:
    return {"gap_max_r": GAP_MAX, **skip_stats(b, b.trigger.notna() & (b.g > GAP_MAX))}


def weights(d: pd.Series, d50: float) -> pd.Series:
    return np.minimum(1.0, d50 / d)


def r_stats(b: pd.DataFrame, d50: float) -> dict:
    """Return per dollar deployed under w = min(1, D50/d) minus the equal-size mean (paired, sessions resampled)."""
    w, r = weights(b.d, d50).to_numpy(float), b.net.to_numpy(float)
    u, inv = np.unique(b.session.to_numpy(), return_inverse=True)
    swr, sw = np.bincount(inv, w * r, len(u)), np.bincount(inv, w, len(u))
    sr, n = np.bincount(inv, r, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    idx = _rng_idx(len(u))
    per_dollar = swr[idx].sum(1) / sw[idx].sum(1)
    dist = per_dollar - sr[idx].sum(1) / n[idx].sum(1)
    sized = float((w * r).sum() / w.sum())
    return {"d50": d50, "n": len(b), "base": float(r.mean()), "sized": sized, "diff": sized - float(r.mean()),
            "p": _p(dist), "sized_lower95": float(np.quantile(per_dollar, 0.05)), "mean_weight": float(w.mean())}


def e_stats(b: pd.DataFrame) -> dict:
    diff = (b.e_net - b.net).to_numpy(float)
    ch = b[b.e_changed]
    return {"n": len(b), "changed": int(b.e_changed.sum()), "base": float(b.net.mean()), "e": float(b.e_net.mean()),
            "diff": float(diff.mean()), "p": _p(boot_mean(b.session.to_numpy(), diff)),
            "e_lower95": float(np.quantile(boot_mean(b.session.to_numpy(), b.e_net.to_numpy(float)), 0.05)),
            "changed_base": float(ch.net.mean()) if len(ch) else None,
            "changed_e": float(ch.e_net.mean()) if len(ch) else None}


def power(name: str, st: dict, minimum: int) -> bool:
    if name in ("W", "G"):
        return st.get("n_kept", 0) >= minimum and st.get("n_skipped", 0) >= minimum
    return st.get("changed" if name == "E" else "n", 0) >= minimum


def train_pass(name: str, st: dict) -> bool:
    return power(name, st, MIN_TRAIN) and (st.get("diff") or 0) > 0 and st.get("p", 1.0) < ALPHA


PROFIT_KEYS = {"W": ("kept", "kept_lower95"), "G": ("kept", "kept_lower95"), "R": ("sized", "sized_lower95"),
               "E": ("e", "e_lower95")}


def profitable_parts(name: str, st: dict) -> tuple[float | None, float | None]:
    """(mean, lower95) of the adopted variant: kept trades (W, G), per dollar (R), all trades (E)."""
    mean_k, low_k = PROFIT_KEYS[name]
    return st.get(mean_k), st.get(low_k)


def run_all(bt: pd.DataFrame, d80: float, d50: float) -> dict:
    return {"W": w_stats(bt, d80), "R": r_stats(bt, d50), "G": g_stats(bt), "E": e_stats(bt)}


def secondary(bt: pd.DataFrame) -> dict:
    q = pd.qcut(bt.d, 5, labels=False, duplicates="drop")
    return {"d_quintiles": {int(k): {"n": int(len(g)), "d_max": float(g.d.max()), "mean_net": float(g.net.mean())}
                            for k, g in bt.groupby(q)},
            "share_gap_fill": float(((bt.trigger.notna()) & (bt.g > 0)).mean())}


def main() -> int:
    bars = Bars()
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True)
    he = halt_events(bars)
    base = pd.DataFrame(event_rows(fe, bars, False) + event_rows(he, bars, True))
    base["split"] = np.where(base.session < D_STAR, "train", "holdout")
    OUT.mkdir(parents=True, exist_ok=True)
    base.to_csv(OUT / "base.csv", index=False)
    bt, bh = base[base.split == "train"], base[base.split == "holdout"]
    d80, d50 = float(bt.d.quantile(Q_SKIP)), float(bt.d.quantile(Q_RISK))   # fixed from training d only
    train = run_all(bt, d80, d50)
    out = {"prereg": "docs/findings/prereg_long_risk_2026-11-12.md", "alpha": ALPHA, "train": train,
           "secondary_train": secondary(bt), "train_pass": {k: train_pass(k, v) for k, v in train.items()},
           "holdout": {}, "adopt": {}, "profitable": {}}
    hold = run_all(bh, d80, d50) if any(out["train_pass"].values()) else {}
    for k, ok in out["train_pass"].items():          # held-out opened only for a training pass
        if not ok:
            out["adopt"][k] = out["profitable"][k] = False
            continue
        h = hold[k]
        out["holdout"][k] = h
        out["adopt"][k] = power(k, h, MIN_HOLD) and (h.get("diff") or 0) > 0
        mean_t, low_t = profitable_parts(k, train[k])
        mean_h, _ = profitable_parts(k, h)
        out["profitable"][k] = bool(out["adopt"][k] and (mean_t or 0) > 0 and (low_t or 0) > 0 and (mean_h or 0) > 0)
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="long-risk-2026-11-12", kind="long-risk",
                                  params={"prereg": out["prereg"]}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
