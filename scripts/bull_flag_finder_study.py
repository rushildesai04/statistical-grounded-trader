"""The owner's "Clean Bull Flag Finder" as a long setup on the alert book (docs/findings/prereg_bull_flag_finder_2026-11-13.md).

    nice -n 10 .venv/bin/python scripts/bull_flag_finder_study.py      # run ONCE (frozen prereg)

The indicator (sgt.fast.bullflag) runs over every stored bar of each symbol-session (extended hours included, state from
the day's first bar). A signal counts when its bar is at or after the event's t0 and closes no later than 15:30 ET. The
EMA9 > EMA21 gate (closes over the day's bars) selects the gated signals. Four candidates: B1 = every gated signal,
B2 = gated signals with strength >= 75, each exited the long-book way (X1: 2R target, else the close 30 minutes after
entry) and the owner's trend way (X2: out at the next open after a close with EMA9 < EMA21, capped at the 15:55 close).
Entry is the next bar's open, skipped when that open is at or below the flag low; the flag low is the stop (checked first
inside a bar); one position per event at a time. Session-clustered bootstrap, Bonferroni over four, held-out opened only
for a training pass. Report only; nothing here changes a live rule.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from long_family_study import Bars, fade_events, halt_events  # noqa: E402

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.fast import bullflag as bf  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402
from sgt.fast.fade import ET  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/bull_flag_finder")
SEED, N_BOOT = 20261113, 10_000                       # prereg: seed 20261113, 10,000 resamples
ALPHA = 0.05 / 4                                      # Bonferroni over the four candidates
MIN_TRAIN = {"B1": 100, "B2": 50}
MIN_HOLD = 30
STRENGTH_CUT = 75.0                                   # the owner's threshold
D_STAR, HOLD_END, FWD_START = "2026-07-01", "2026-10-29", "2026-10-30"
BAR = pt.BAR
HOLD = pd.Timedelta(minutes=30)
RR = 2.0
# Candidate -> (universe base, exit rule, EMA gate on?). The four pre-registered candidates all gate.
CANDIDATES = {"B1X1": ("B1", "X1", True), "B1X2": ("B1", "X2", True),
              "B2X1": ("B2", "X1", True), "B2X2": ("B2", "X2", True)}
# Secondary 1: the same four without the EMA gate (does 9 > 21 add anything?).
NOGATE = {f"{k}_nogate": (base, exit_, False) for k, (base, exit_, _) in CANDIDATES.items()}
ALL_CANDS = {**CANDIDATES, **NOGATE}


# -- bars (full day, extended hours; the indicator's contract) ---------------------------------------------------
class FullBars:
    """Every stored bar of a symbol-session (no segment filter), ts in UTC, time-ordered. Bounded cache."""

    def __init__(self) -> None:
        self.store, self.cache = ParquetBarStore("data/alpaca"), {}

    def __call__(self, symbol: str, session: str) -> pd.DataFrame:
        k = (symbol, session)
        if k not in self.cache:
            d = date.fromisoformat(session)
            f = self.store.read_frame(symbol, d, d)
            self.cache[k] = (f.assign(ts=pd.to_datetime(f.ts, utc=True)).sort_values("ts").reset_index(drop=True)
                             if len(f) else f)
            if len(self.cache) > 400:
                self.cache.pop(next(iter(self.cache)))
        return self.cache[k]


def et_bar(session: str, hh: int, mm: int) -> pd.Timestamp:
    d = date.fromisoformat(session)
    return pd.Timestamp(datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)).tz_convert("UTC")


# -- per-event signals and the two exit rules --------------------------------------------------------------------
def gated_signals(f: pd.DataFrame, t0: pd.Timestamp, session: str) -> list[tuple[bf.Signal, bool]]:
    """Counted signals (bar at/after t0, closing no later than 15:30 ET) paired with the EMA9 > EMA21 gate."""
    sigs = bf.find_signals(f)
    if not sigs:
        return []
    close = f.close.to_numpy(float)
    ema9, ema21 = bf.ema(close, 9), bf.ema(close, 21)
    cutoff = et_bar(session, 15, 30)
    out = []
    for s in sigs:
        s_ts = f.ts.iloc[s.i]
        if s_ts < t0 or (s_ts + BAR) > cutoff:        # close = ts + 1 min must be <= 15:30 ET
            continue
        out.append((s, bool(ema9[s.i] > ema21[s.i])))
    return out


def x1_trade(f: pd.DataFrame, s: bf.Signal, cost: float) -> dict | None:
    """Long-book standard: next-open entry, 2R target, else the close 30 minutes after entry; flag-low stop first."""
    entry_i = s.i + 1
    if entry_i >= len(f):
        return None
    entry_ts = f.ts.iloc[entry_i]
    setup = pt.Setup("BF", "long", entry_ts, None, float(s.flag_low))     # market at the next bar's open
    t = pt.simulate(f, setup, hold=HOLD, rr=RR)                           # None when the open is at/below the stop
    if t is None:
        return None
    return _row(s, t.entry_ts, t.entry, t.exit_ts, t.exit, t.reason, t.gross, cost)


def make_x2(f: pd.DataFrame):
    """X2 exit closure over this frame's EMA9/EMA21: no target; out at the next open after a close with EMA9 < EMA21,
    capped at the 15:55 ET bar close; the flag-low stop is still checked first inside each bar."""
    close = f.close.to_numpy(float)
    below = bf.ema(close, 9) < bf.ema(close, 21)
    op, lo, cl = (f[c].to_numpy(float) for c in ("open", "low", "close"))
    ts = f.ts
    n = len(f)

    def x2(f_: pd.DataFrame, s: bf.Signal, cost: float) -> dict | None:
        entry_i = s.i + 1
        if entry_i >= n:
            return None
        entry, stop = float(op[entry_i]), float(s.flag_low)
        if entry <= stop:
            return None
        cap_ts = et_bar(ts.iloc[0].tz_convert(ET).date().isoformat(), 15, 55)
        pending = False
        for j in range(entry_i, n):
            if ts.iloc[j] > cap_ts:
                break
            if pending:                                                  # trend fired last bar: out at this open
                return _row(s, ts.iloc[entry_i], entry, ts.iloc[j], float(op[j]), "trend",
                            float(op[j]) / entry - 1, cost)
            if lo[j] <= stop:                                            # stop first inside the bar
                px = min(stop, float(op[j]))
                return _row(s, ts.iloc[entry_i], entry, ts.iloc[j], px, "stop", px / entry - 1, cost)
            if j + 1 >= n or ts.iloc[j + 1] > cap_ts:                    # the 15:55 cap: out at its close
                return _row(s, ts.iloc[entry_i], entry, ts.iloc[j], float(cl[j]), "time", float(cl[j]) / entry - 1, cost)
            if below[j]:
                pending = True
        last = n - 1                                                     # no bar within the cap after entry: last close
        return _row(s, ts.iloc[entry_i], entry, ts.iloc[last], float(cl[last]), "time", float(cl[last]) / entry - 1, cost)

    return x2


def _row(s: bf.Signal, entry_ts, entry, exit_ts, exit_px, reason, gross, cost) -> dict:
    return {"signal_i": s.i, "flag_low": float(s.flag_low), "flag_high": float(s.flag_high),
            "strength": float(s.strength), "entry_ts": entry_ts, "entry": float(entry), "exit_ts": exit_ts,
            "exit": float(exit_px), "reason": reason, "gross": float(gross), "net": float(gross) - float(cost)}


def book(f: pd.DataFrame, signals: list[bf.Signal], exit_fn, cost: float) -> list[dict]:
    """One position per event at a time: a signal while a trade is open is ignored; later signals trade."""
    trades, open_until = [], None
    for s in signals:
        s_ts = f.ts.iloc[s.i]
        if open_until is not None and s_ts < open_until:                 # entry (s.i+1) would overlap the open trade
            continue
        tr = exit_fn(f, s, cost)
        if tr is None:
            continue
        trades.append(tr)
        open_until = tr["exit_ts"]
    return trades


def universe(signals: list[tuple[bf.Signal, bool]], base: str, use_gate: bool) -> list[bf.Signal]:
    out = []
    for s, gated in signals:
        if use_gate and not gated:
            continue
        if base == "B2" and s.strength < STRENGTH_CUT:
            continue
        out.append(s)
    return out


# -- trades for every candidate over every event -----------------------------------------------------------------
def event_rows(ev: pd.DataFrame, bars: FullBars) -> list[dict]:
    rows = []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        sigs = gated_signals(f, pd.Timestamp(r.t0), r.session)
        if not sigs:
            continue
        x2 = make_x2(f)
        exits = {"X1": x1_trade, "X2": x2}
        for cand, (base, exit_, use_gate) in ALL_CANDS.items():
            uni = universe(sigs, base, use_gate)
            for tr in book(f, uni, exits[exit_], float(r.cost_rt)):
                rows.append({"candidate": cand, "symbol": r.symbol, "session": r.session, "halt": bool(r.halt),
                             "signal_ts": f.ts.iloc[tr["signal_i"]], **tr})
    return rows


def green_rows(ev: pd.DataFrame, bars: FullBars) -> list[dict]:
    """Secondary 3: the bar after a gated signal vs the bar after any gated bar (owner's 'explosive green candle')."""
    rows = []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        close = f.close.to_numpy(float)
        op = f.open.to_numpy(float)
        ema9, ema21 = bf.ema(close, 9), bf.ema(close, 21)
        cutoff, t0, n = et_bar(r.session, 15, 30), pd.Timestamp(r.t0), len(f)
        sig_idx = {s.i for s, gated in gated_signals(f, t0, r.session) if gated}
        for j in range(n - 1):
            s_ts = f.ts.iloc[j]
            if s_ts < t0 or (s_ts + BAR) > cutoff or not (ema9[j] > ema21[j]):
                continue
            nxt = j + 1
            rows.append({"session": r.session, "is_signal": j in sig_idx,
                         "next_green": bool(close[nxt] > op[nxt]), "next_ret": float(close[nxt] / op[nxt] - 1)})
    return rows


# -- statistics --------------------------------------------------------------------------------------------------
def boot_mean(sessions: np.ndarray, vals: np.ndarray) -> np.ndarray:
    """Session-clustered bootstrap of the per-trade mean (prereg seed 20261113)."""
    u, inv = np.unique(sessions, return_inverse=True)
    s = np.bincount(inv, vals, len(u))
    n = np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        return s[b].sum(1) / n[b].sum(1)


def _p(m: np.ndarray) -> float:
    """One-sided p: share of resampled means <= 0; an undefined resample counts against (fail closed)."""
    return float(((m <= 0) | np.isnan(m)).mean())


def stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    m = boot_mean(df.session.to_numpy(), df.net.to_numpy(float))
    wins, losses = df[df.net > 0], df[df.net <= 0]
    return {"n": int(len(df)), "mean": float(df.net.mean()), "median": float(df.net.median()),
            "win": float((df.net > 0).mean()), "p": _p(m), "lower95": float(np.quantile(m, 0.05)),
            "exits": df.reason.value_counts(normalize=True).round(4).to_dict(),
            "mean_winner": float(wins.net.mean()) if len(wins) else None,
            "mean_loser": float(losses.net.mean()) if len(losses) else None}


def train_pass(cand: str, st: dict) -> bool:
    base = cand[:2]
    return st.get("n", 0) >= MIN_TRAIN[base] and st.get("mean", 0.0) > 0 and st.get("p", 1.0) < ALPHA


def green_stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"signals": 0, "gated_bars": 0}
    sig, base = df[df.is_signal], df
    return {"signals": int(len(sig)), "gated_bars": int(len(base)),
            "signal_next_green": float(sig.next_green.mean()) if len(sig) else None,
            "signal_next_ret": float(sig.next_ret.mean()) if len(sig) else None,
            "gated_next_green": float(base.next_green.mean()), "gated_next_ret": float(base.next_ret.mean())}


def main() -> int:
    bars = Bars()                                     # rth bars: used only to find halt reopens (long_family)
    full = FullBars()                                 # full-day bars: the indicator's input
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True).assign(halt=False)
    he = halt_events(bars).assign(halt=True)
    ev = pd.concat([fe[["symbol", "session", "t0", "cost_rt", "halt"]], he], ignore_index=True)

    trades = pd.DataFrame(event_rows(ev, full))
    trades["split"] = np.select([trades.session < D_STAR, trades.session <= HOLD_END],
                                ["train", "holdout"], default="forward")
    OUT.mkdir(parents=True, exist_ok=True)
    trades.to_csv(OUT / "trades.csv", index=False)

    def part(cand: str, split: str) -> pd.DataFrame:
        return trades[(trades.candidate == cand) & (trades.split == split)]

    train = {c: stats(part(c, "train")) for c in CANDIDATES}
    out = {"prereg": "docs/findings/prereg_bull_flag_finder_2026-11-13.md", "alpha": ALPHA,
           "train": train, "train_pass": {c: train_pass(c, train[c]) for c in CANDIDATES},
           "holdout": {}, "candidate_adopt": {}, "profitable": {}, "adopt": None}
    for c in CANDIDATES:
        if not out["train_pass"][c]:
            out["candidate_adopt"][c] = out["profitable"][c] = False
            continue
        h = stats(part(c, "holdout"))                 # held-out opened only for a training pass
        out["holdout"][c] = h
        adopt = h.get("n", 0) >= MIN_HOLD and h.get("mean", 0.0) > 0
        out["candidate_adopt"][c] = adopt
        out["profitable"][c] = bool(adopt and train[c]["mean"] > 0 and train[c]["lower95"] > 0 and h["mean"] > 0)
    winners = [c for c in CANDIDATES if out["candidate_adopt"].get(c)]
    if winners:                                       # more than one: higher training mean, ties -> X1
        out["adopt"] = min(winners, key=lambda c: (-train[c]["mean"], 0 if c.endswith("X1") else 1))

    green = pd.DataFrame(green_rows(ev, full))
    out["secondary"] = {
        "1_nogate_train": {c: stats(part(c, "train")) for c in NOGATE},
        "2_forward": {c: stats(part(c, "forward")) for c in CANDIDATES},
        "3_green_candle": green_stats(green),
        "4_exits_train": {c: {"exits": train[c].get("exits", {}), "mean_winner": train[c].get("mean_winner"),
                              "mean_loser": train[c].get("mean_loser")} for c in CANDIDATES}}
    print(json.dumps(out, indent=2, default=str))
    (OUT / "result.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="bull-flag-finder-2026-11-13", kind="bull-flag-finder",
                                  params={"prereg": out["prereg"]}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
