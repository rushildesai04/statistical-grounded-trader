"""Cover the timed exit with a limit at the bid (prereg_fade_passive_exit_2026-11-08).

    python scripts/fade_passive_exit_study.py      # run once: training gate + held-out secondary
"""
from __future__ import annotations

import json
import sys
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from alert_fade_test import HOLD_MIN, STOP  # noqa: E402
from fade_spread_audit import CACHE, trades  # noqa: E402

from sgt.db.session import get_session, init_db  # noqa: E402
from sgt.decision import trial_ledger  # noqa: E402
from sgt.intraday.store import ParquetBarStore  # noqa: E402

OUT = Path("reports/fade_passive_exit")
SEED, N_BOOT, ALPHA, TIMEOUT = 20261108, 10_000, 0.05, 2
ET = "America/New_York"


def simulate(bars: pd.DataFrame, entry: float, entry_ts: pd.Timestamp, mid: float | None, hs: float | None,
             timeout: int = TIMEOUT) -> dict:
    """One trade: baseline (ask at t) vs passive (bid at t, ``timeout`` minutes). ``bars`` = the day's RTH bars, ts UTC."""
    t = entry_ts + pd.Timedelta(minutes=HOLD_MIN)
    hold = bars[(bars.ts >= entry_ts) & (bars.ts < t)]
    if hold.empty or (hold.high.to_numpy() >= entry * (1 + STOP)).any():
        return {"kind": "stopped_or_empty", "delta": 0.0, "filled": None}
    end = t + pd.Timedelta(minutes=timeout)
    if mid is None or hs is None or not np.isfinite(mid) or not np.isfinite(hs) or mid <= 0:
        return {"kind": "no_quote", "delta": 0.0, "filled": None}
    if end.tz_convert(ET).time() > time(16, 0):
        return {"kind": "near_close", "delta": 0.0, "filled": None}
    bid, ask = mid * (1 - hs), mid * (1 + hs)
    w = bars[(bars.ts >= t) & (bars.ts < end)]
    if not w.empty and (w.low.to_numpy() < bid).any():
        px, filled = bid, True
    else:
        px, filled = (float(w.close.iloc[-1]) * (1 + hs) if not w.empty else ask), False
    return {"kind": "timed", "delta": (ask - px) / entry, "filled": filled}


def run(name: str, store: ParquetBarStore, q: dict, timeouts: tuple[int, ...]) -> pd.DataFrame:
    t = trades()
    t = t[t.split == name]
    rows = []
    for r in t.itertuples():
        d = date.fromisoformat(str(r.session))
        f = store.read_frame(r.symbol, d, d)
        if f.empty:
            continue
        f = f.assign(ts=pd.to_datetime(f.ts, utc=True))
        f = f[f.segment == "rth"]
        x = q.get((r.symbol, str(r.session)))
        for timeout in timeouts:
            res = simulate(f, float(r.entry), pd.Timestamp(r.entry_ts), *(x if x else (None, None)), timeout=timeout)
            rows.append({"symbol": r.symbol, "session": str(r.session), "entry": r.entry, "net": r.net,
                         "timeout": timeout, **res})
    return pd.DataFrame(rows)


def gate(d: pd.DataFrame) -> dict:
    u, inv = np.unique(d.session.to_numpy(), return_inverse=True)
    s, n = np.bincount(inv, d.delta.to_numpy(float), len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    boot = s[b].sum(1) / n[b].sum(1)
    tm = d[d.kind == "timed"]
    band = pd.cut(d.entry, [1, 2, 5, 10, 50.01], labels=["$1-2", "$2-5", "$5-10", "$10-50"], right=False)
    res = {"n": len(d), "kinds": d.kind.value_counts().to_dict(), "mean_delta": float(d.delta.mean()),
           "lower": float(np.quantile(boot, ALPHA)), "fill_rate": float(tm.filled.mean()) if len(tm) else None,
           "delta_filled": float(tm.delta[tm.filled == True].mean()) if len(tm) else None,  # noqa: E712
           "delta_unfilled": float(tm.delta[tm.filled == False].mean()) if len(tm) else None,  # noqa: E712
           "by_band": {str(k): float(v) for k, v in d.groupby(band, observed=True).delta.mean().items()},
           "net": float(d.net.mean()), "net_passive": float((d.net + d.delta).mean())}
    res["pass"] = bool(res["lower"] > 0)
    return res


def main() -> int:
    store = ParquetBarStore("data/alpaca")
    c = pd.read_csv(CACHE)
    c = c[c.leg == "exit"]
    q = {(r.symbol, str(r.session)): (r.mid, r.hs_med) for r in c.itertuples() if pd.notna(r.mid)}
    out = {}
    for name in ("dev", "holdout"):
        out[name] = {}
        allt = run(name, store, q, (TIMEOUT, 1, 5))
        for timeout in (TIMEOUT, 1, 5):
            d = allt[allt.timeout == timeout].reset_index(drop=True)
            r = gate(d)
            out[name][f"{timeout}m"] = r
            tag = " (decides)" if name == "dev" and timeout == TIMEOUT else ""
            print(f"{name:7s} {timeout}m: n{r['n']} timed {r['kinds'].get('timed', 0)} fill {r['fill_rate']:.0%} "
                  f"delta {r['mean_delta']:+.3%}/trade (lower {r['lower']:+.3%}) filled {r['delta_filled']:+.2%} "
                  f"unfilled {r['delta_unfilled']:+.2%} | net {r['net']:+.2%} -> {r['net_passive']:+.2%} "
                  f"-> {'PASS' if r['pass'] else 'fail'}{tag}")
            if timeout == TIMEOUT:
                print(f"        by band {({k: round(v * 100, 3) for k, v in r['by_band'].items()})}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-passive-exit-2026-11-08-dev", kind="fade-passive-exit-dev",
                                  params={"prereg": "docs/findings/prereg_fade_passive_exit_2026-11-08.md"},
                                  metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
