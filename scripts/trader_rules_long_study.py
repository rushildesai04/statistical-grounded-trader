"""The trader's five long rules on the alert long book (docs/findings/prereg_trader_rules_long_2026-11-12.md).

    nice -n 10 .venv/bin/python scripts/trader_rules_long_study.py      # run ONCE (frozen prereg)

Rules (the trader, 2026-11-11): 9 EMA crosses 24 EMA, volume spike, MACD positive, PSAR green, price above VWAP; on
GEVO's target exit: "depends if MACD was positive or negative". Three candidates on the live long book's events:
F5 (the rules as a gate on our setups), T5 (the rules as the entry), XM (hold past the 2R target while the MACD
histogram is positive). Report only; nothing here changes a live rule.
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

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402
from sgt.fast.signals import PSAR_MAX, PSAR_STEP  # noqa: E402

OUT = Path("reports/trader_rules_long")
SEED, N_BOOT = 20261112, 10_000
ALPHA = 0.05 / 3                                     # Bonferroni over the three candidates
MIN_TRAIN, MIN_HOLD = 100, 30
SPIKE, SPIKE_BACK, VOL_BASE = 5.0, 5, 10             # decision/trader_rules.py defaults
COND = ("c1", "c2", "c3", "c4", "c5")
HOLD30 = pd.Timedelta(minutes=30)


def psar_up(h: np.ndarray, lo: np.ndarray) -> np.ndarray:
    """PSAR side per bar: True when bar k is in an up-trend, exactly ``signals._psar_side(h[:k+1], lo[:k+1])``."""
    n = len(h)
    out = np.zeros(n, dtype=bool)
    if n < 2:
        return out
    bull = h[1] >= h[0]
    ep = max(h[0], h[1]) if bull else min(lo[0], lo[1])
    sar = lo[0] if bull else h[0]
    af = PSAR_STEP
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, lo[i - 1], lo[i - 2] if i >= 2 else lo[i - 1])
            if lo[i] < sar:
                bull, sar, ep, af = False, ep, lo[i], PSAR_STEP
            elif h[i] > ep:
                ep, af = h[i], min(af + PSAR_STEP, PSAR_MAX)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > sar:
                bull, sar, ep, af = True, ep, h[i], PSAR_STEP
            elif lo[i] < ep:
                ep, af = lo[i], min(af + PSAR_STEP, PSAR_MAX)
        out[i] = bull
    return out


def conditions(f: pd.DataFrame) -> pd.DataFrame:
    """C1-C5 at each bar's close (causal); NaN fails a condition."""
    c, h, lo, v = (f[k].astype(float).reset_index(drop=True) for k in ("close", "high", "low", "volume"))
    ema9, ema24 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=24, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    hist = macd - macd.ewm(span=9, adjust=False).mean()
    base = v.shift(1).rolling(VOL_BASE, min_periods=5).mean()
    spike = (v / base.where(base > 0)).rolling(SPIKE_BACK, min_periods=1).max()
    cum = v.cumsum()
    vwap = ((h + lo + c) / 3 * v).cumsum() / cum.where(cum > 0)
    d = pd.DataFrame({"c1": ema9 > ema24, "c2": spike >= SPIKE, "c3": hist > 0,
                      "c4": psar_up(h.to_numpy(), lo.to_numpy()), "c5": c > vwap,
                      "cross": (ema9 > ema24) & (ema9.shift(1) <= ema24.shift(1)), "hist": hist})
    d["all5"] = d[list(COND)].all(axis=1)
    return d


def bar_index(f: pd.DataFrame, ts: pd.Timestamp) -> int:
    i = np.flatnonzero((f.ts == ts).to_numpy())
    if not len(i):
        raise ValueError(f"no bar at {ts}")
    return int(i[0])


def gate(cond: pd.DataFrame, fill_i: int) -> dict:
    """F5: the five conditions at the close of the bar before the fill bar (none before the first bar: skipped)."""
    k = fill_i - 1
    if k < 0:
        return {c: False for c in (*COND, "all5")}
    return {c: bool(cond[c].iloc[k]) for c in (*COND, "all5")}


def t5_setup(f: pd.DataFrame, cond: pd.DataFrame, t0: pd.Timestamp) -> pt.Setup | None:
    """T5: the first bar in [t0, t0 + WATCH) where EMA 9 crosses above EMA 24 and C2-C5 hold; market buy at the next
    bar, stop = low of bars k-2..k (trader_rules.trade)."""
    ok = (f.ts >= t0).to_numpy() & (f.ts < t0 + pt.WATCH).to_numpy()
    sig = ok & cond.cross.to_numpy() & cond[["c2", "c3", "c4", "c5"]].all(axis=1).to_numpy()
    hits = np.flatnonzero(sig)
    if not len(hits):
        return None
    k = int(hits[0])
    stop = float(f.low.iloc[max(0, k - 2):k + 1].min())
    return pt.Setup("T5", "long", f.ts.iloc[k] + pt.BAR, None, stop, {"signal_i": k})


def mark30(f: pd.DataFrame, entry_ts: pd.Timestamp, entry: float) -> float:
    """Secondary: no stop, no target, the close of the last bar within 30 minutes of the entry."""
    p = f[(f.ts >= entry_ts) & (f.ts < entry_ts + HOLD30)]
    return float(p.close.iloc[-1]) / entry - 1


def xm_exit(f: pd.DataFrame, hist: np.ndarray, stop: float, entry: float, target_gross: float,
            m: int) -> tuple[float, str]:
    """XM gross on a target trade whose target bar is ``m``: held while the histogram stays > 0 (see prereg)."""
    if m < 1 or not hist[m - 1] > 0:
        return target_gross, "target"
    lo, op, cl = (f[k].to_numpy(float) for k in ("low", "open", "close"))
    n = len(f)
    for j in range(m, n):
        if lo[j] <= stop:
            px = stop if j == m else min(stop, op[j])
            return px / entry - 1, "stop"
        if not hist[j] > 0:
            return (op[j + 1] if j + 1 < n else cl[j]) / entry - 1, "macd"
    return cl[n - 1] / entry - 1, "close"


def event_rows(ev: pd.DataFrame, bars: Bars, halt: bool) -> tuple[list[dict], list[dict]]:
    base, t5 = [], []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        cond = conditions(f)
        cut, cost, t0 = latest(r.session), float(r.cost_rt), pd.Timestamp(r.t0)
        hit = first_fill(f, t0, halt, cut)
        if hit is not None:
            s, t = hit
            fill_i = bar_index(f, t.entry_ts)
            row = {"symbol": r.symbol, "session": r.session, "halt": halt, "kind": s.kind, "entry_ts": t.entry_ts,
                   "reason": t.reason, "net": t.gross - cost, **gate(cond, fill_i)}
            xm, why = t.gross, "not_target"
            if t.reason == "target":
                xm, why = xm_exit(f, cond["hist"].to_numpy(float), s.stop, t.entry, t.gross, bar_index(f, t.exit_ts))
            row.update(xm=xm - cost, xm_why=why)
            base.append(row)
        s5 = t5_setup(f, cond, t0)
        if s5 is not None:
            t = pt.simulate(f, s5, latest=cut)
            if t is not None:
                t5.append({"symbol": r.symbol, "session": r.session, "halt": halt, "entry_ts": t.entry_ts,
                           "reason": t.reason, "net": t.gross - cost,
                           "nostop30": mark30(f, t.entry_ts, t.entry) - cost})
    return base, t5


def _rng_idx(n_sessions: int) -> np.ndarray:
    return np.random.default_rng(SEED).integers(0, n_sessions, size=(N_BOOT, n_sessions))


def boot_mean(sess: np.ndarray, val: np.ndarray) -> np.ndarray:
    """Session-clustered bootstrap of a per-trade mean."""
    u, inv = np.unique(sess, return_inverse=True)
    s, n = np.bincount(inv, val, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = _rng_idx(len(u))
    return s[b].sum(1) / n[b].sum(1)


def boot_groups(sa: np.ndarray, va: np.ndarray, sb: np.ndarray, vb: np.ndarray) -> np.ndarray:
    """Session-clustered bootstrap of mean(a) - mean(b), sessions resampled jointly for both groups."""
    u = np.unique(np.concatenate([sa, sb]))
    ia, ib = np.searchsorted(u, sa), np.searchsorted(u, sb)
    s_a, n_a = np.bincount(ia, va, len(u)), np.bincount(ia, minlength=len(u)).astype(float)
    s_b, n_b = np.bincount(ib, vb, len(u)), np.bincount(ib, minlength=len(u)).astype(float)
    b = _rng_idx(len(u))
    return s_a[b].sum(1) / n_a[b].sum(1) - s_b[b].sum(1) / n_b[b].sum(1)


def _p(d: np.ndarray) -> float:
    return float((d <= 0).mean())


def f5_stats(b: pd.DataFrame, col: str = "all5") -> dict:
    kept, skip = b[b[col]], b[~b[col]]
    out = {"n_kept": len(kept), "n_skipped": len(skip), "base": float(b.net.mean()),
           "kept": float(kept.net.mean()) if len(kept) else None, "skipped": float(skip.net.mean()) if len(skip) else None}
    if len(kept) and len(skip):
        d = boot_groups(kept.session.to_numpy(), kept.net.to_numpy(float), skip.session.to_numpy(), skip.net.to_numpy(float))
        out.update(diff=out["kept"] - out["skipped"], p=_p(d),
                   kept_lower95=float(np.quantile(boot_mean(kept.session.to_numpy(), kept.net.to_numpy(float)), 0.05)))
    return out


def t5_stats(x: pd.DataFrame, b: pd.DataFrame) -> dict:
    out = {"n": len(x), "t5": float(x.net.mean()) if len(x) else None, "base": float(b.net.mean()),
           "nostop30": float(x.nostop30.mean()) if len(x) else None,
           "reasons": x.reason.value_counts().to_dict() if len(x) else {}}
    if len(x):
        d = boot_groups(x.session.to_numpy(), x.net.to_numpy(float), b.session.to_numpy(), b.net.to_numpy(float))
        out.update(diff=out["t5"] - out["base"], p=_p(d), win=float((x.net > 0).mean()),
                   t5_lower95=float(np.quantile(boot_mean(x.session.to_numpy(), x.net.to_numpy(float)), 0.05)))
    return out


def xm_stats(b: pd.DataFrame) -> dict:
    tg = b[b.reason == "target"]
    diff = (b.xm - b.net).to_numpy(float)
    return {"n": len(b), "targets": len(tg), "base": float(b.net.mean()), "xm": float(b.xm.mean()),
            "diff": float(diff.mean()), "p": _p(boot_mean(b.session.to_numpy(), diff)),
            "xm_lower95": float(np.quantile(boot_mean(b.session.to_numpy(), b.xm.to_numpy(float)), 0.05)),
            "on_targets": {"target_exit": float(tg.net.mean()) if len(tg) else None,
                           "xm": float(tg.xm.mean()) if len(tg) else None,
                           "why": tg.xm_why.value_counts().to_dict() if len(tg) else {}}}


def train_pass(name: str, st: dict) -> bool:
    n = {"F5": st.get("n_kept", 0), "T5": st.get("n", 0), "XM": st.get("targets", 0)}[name]
    return n >= MIN_TRAIN and (st.get("diff") or 0) > 0 and st.get("p", 1.0) < ALPHA


def main() -> int:
    bars = Bars()
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True)
    he = halt_events(bars)
    b1, x1 = event_rows(fe, bars, False)
    b2, x2 = event_rows(he, bars, True)
    base, t5 = pd.DataFrame(b1 + b2), pd.DataFrame(x1 + x2)
    for d in (base, t5):
        d["split"] = np.where(d.session < D_STAR, "train", "holdout")
    OUT.mkdir(parents=True, exist_ok=True)
    base.to_csv(OUT / "base.csv", index=False)
    t5.to_csv(OUT / "t5.csv", index=False)
    bt, xt = base[base.split == "train"], t5[t5.split == "train"]
    train = {"F5": f5_stats(bt), "T5": t5_stats(xt, bt), "XM": xm_stats(bt)}
    secondary = {"single_conditions": {c: f5_stats(bt, c) for c in COND}}
    out = {"prereg": "docs/findings/prereg_trader_rules_long_2026-11-12.md", "alpha": ALPHA, "train": train,
           "secondary_train": secondary, "train_pass": {k: train_pass(k, v) for k, v in train.items()},
           "holdout": {}, "adopt": {}, "profitable": {}}
    bh, xh = base[base.split == "holdout"], t5[t5.split == "holdout"]
    for k, ok in out["train_pass"].items():          # held-out opened only for a training pass
        if not ok:
            out["adopt"][k] = out["profitable"][k] = False
            continue
        h = {"F5": lambda: f5_stats(bh), "T5": lambda: t5_stats(xh, bh), "XM": lambda: xm_stats(bh)}[k]()
        n = {"F5": h.get("n_kept", 0), "T5": h.get("n", 0), "XM": h.get("targets", 0)}[k]
        out["holdout"][k] = h
        out["adopt"][k] = n >= MIN_HOLD and (h.get("diff") or 0) > 0
        mean_t, low_t, mean_h = {"F5": ("kept", "kept_lower95", "kept"), "T5": ("t5", "t5_lower95", "t5"),
                                 "XM": ("xm", "xm_lower95", "xm")}[k]
        out["profitable"][k] = bool(out["adopt"][k] and train[k][mean_t] > 0 and train[k][low_t] > 0
                                    and (h.get(mean_h) or 0) > 0)
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="trader-rules-long-2026-11-12", kind="trader-rules-long",
                                  params={"prereg": out["prereg"]}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
