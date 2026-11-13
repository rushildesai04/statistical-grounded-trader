"""BoS while EMA 9 > EMA 21 as a long setup on alerted stocks (docs/findings/prereg_bos_ema_2026-11-13.md).

    nice -n 10 .venv/bin/python scripts/bos_ema_study.py      # run ONCE (frozen prereg)

Population, entry, costs, exits and bootstrap are the bull flag study's own functions (same pre-registered rules);
only the signal (sgt.fast.structure) and the stop (the latest confirmed pivot low) differ. Report only."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bull_flag_finder_study as bfs  # noqa: E402
from long_family_study import Bars, fade_events, halt_events  # noqa: E402
from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.fast import bullflag as bf  # noqa: E402
from sgt.fast import structure as st  # noqa: E402

OUT = Path("reports/bos_ema")
ALPHA = 0.05 / 2
MIN_TRAIN, MIN_HOLD = 100, 30
EXPLOSIVE = 0.01
# candidate -> (kind, exit, EMA gate)
CANDIDATES = {"S1X1": ("BOS", "X1", True), "S1X2": ("BOS", "X2", True)}
SECONDARY = {"CHOCH_X1": ("CHOCH", "X1", True), "CHOCH_X2": ("CHOCH", "X2", True),
             "S1X1_nogate": ("BOS", "X1", False), "S1X2_nogate": ("BOS", "X2", False)}
ALL = {**CANDIDATES, **SECONDARY}


@dataclass(frozen=True, slots=True)
class Sig:                       # the fields the bull flag study's exits read
    i: int
    flag_low: float              # the stop: latest confirmed pivot low
    flag_high: float             # the broken level
    strength: float = 0.0


def signals(f: pd.DataFrame, t0: pd.Timestamp, session: str) -> list[tuple[str, bool, Sig]]:
    """Counted bullish breaks (bar at/after t0, closing by 15:30 ET) with a stop: (kind, EMA gate, signal)."""
    close = f.close.to_numpy(float)
    ema9, ema21 = bf.ema(close, 9), bf.ema(close, 21)
    cutoff = bfs.et_bar(session, 15, 30)
    out = []
    for b in st.bullish_breaks(f):
        ts = f.ts.iloc[b.i]
        if ts < t0 or ts + bfs.BAR > cutoff or b.stop is None:
            continue
        out.append((b.kind, bool(ema9[b.i] > ema21[b.i]), Sig(b.i, b.stop, b.level)))
    return out


def event_rows(ev: pd.DataFrame, bars) -> tuple[list[dict], list[dict]]:
    rows, green = [], []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        sigs = signals(f, pd.Timestamp(r.t0), r.session)
        exits = {"X1": bfs.x1_trade, "X2": bfs.make_x2(f)}
        for cand, (kind, exit_, gate) in ALL.items():
            uni = [s for k, g, s in sigs if k == kind and (g or not gate)]
            for tr in bfs.book(f, uni, exits[exit_], float(r.cost_rt)):
                rows.append({"candidate": cand, "symbol": r.symbol, "session": r.session, **tr})
        # secondary 1: the bar after a gated BoS vs the bar after any gated bar
        close, op = f.close.to_numpy(float), f.open.to_numpy(float)
        ema9, ema21 = bf.ema(close, 9), bf.ema(close, 21)
        cutoff, t0 = bfs.et_bar(r.session, 15, 30), pd.Timestamp(r.t0)
        hit = {s.i for k, g, s in sigs if k == "BOS" and g}
        for j in range(len(f) - 1):
            ts = f.ts.iloc[j]
            if ts < t0 or ts + bfs.BAR > cutoff or not ema9[j] > ema21[j]:
                continue
            ret = float(close[j + 1] / op[j + 1] - 1)
            green.append({"session": r.session, "is_signal": j in hit, "green": ret > 0, "explosive": ret >= EXPLOSIVE,
                          "ret": ret})
    return rows, green


def diff_p(g: pd.DataFrame, col: str) -> dict:
    """Signal-bar mean minus all-gated-bar mean of ``col``; session bootstrap, p = share of differences <= 0."""
    u, inv = np.unique(g.session.to_numpy(), return_inverse=True)
    v, s = g[col].to_numpy(float), g.is_signal.to_numpy(bool)
    a, na = np.bincount(inv, v * s, len(u)), np.bincount(inv, s.astype(float), len(u))
    b, nb = np.bincount(inv, v, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    idx = np.random.default_rng(bfs.SEED).integers(0, len(u), size=(bfs.N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        d = a[idx].sum(1) / na[idx].sum(1) - b[idx].sum(1) / nb[idx].sum(1)
    return {"signal": float(v[s].mean()) if s.any() else None, "all_gated": float(v.mean()),
            "p": float(((d <= 0) | np.isnan(d)).mean())}


def main() -> int:
    full = bfs.FullBars()
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True).assign(halt=False)
    ev = pd.concat([fe[["symbol", "session", "t0", "cost_rt", "halt"]], halt_events(Bars()).assign(halt=True)],
                   ignore_index=True)
    rows, green = event_rows(ev, full)
    t = pd.DataFrame(rows)
    t["split"] = np.select([t.session < bfs.D_STAR, t.session <= bfs.HOLD_END], ["train", "holdout"], "forward")
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "trades.csv", index=False)

    def part(c, split):
        return t[(t.candidate == c) & (t.split == split)]

    train = {c: bfs.stats(part(c, "train")) for c in CANDIDATES}
    out = {"prereg": "docs/findings/prereg_bos_ema_2026-11-13.md", "alpha": ALPHA, "train": train,
           "train_pass": {c: bool(s.get("n", 0) >= MIN_TRAIN and s.get("mean", 0) > 0 and s.get("p", 1) < ALPHA)
                          for c, s in train.items()}, "holdout": {}, "adopt": None}
    ok = []
    for c in CANDIDATES:
        if out["train_pass"][c]:                      # held-out opened only for a training pass
            h = out["holdout"][c] = bfs.stats(part(c, "holdout"))
            if h.get("n", 0) >= MIN_HOLD and h.get("mean", 0) > 0:
                ok.append(c)
    if ok:
        out["adopt"] = min(ok, key=lambda c: (-train[c]["mean"], 0 if c.endswith("X1") else 1))
    g = pd.DataFrame(green)
    g = g[g.session < bfs.D_STAR]
    out["secondary"] = {
        "1_next_bar_train": {"signals": int(g.is_signal.sum()), "gated_bars": int(len(g)),
                             **{k: diff_p(g, k) for k in ("green", "explosive", "ret")}},
        "2_train": {c: bfs.stats(part(c, "train")) for c in SECONDARY},
        "3_forward": {c: bfs.stats(part(c, "forward")) for c in CANDIDATES}}
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="bos-ema-2026-11-13", kind="bos-ema", params={"prereg": out["prereg"]},
                                  metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
