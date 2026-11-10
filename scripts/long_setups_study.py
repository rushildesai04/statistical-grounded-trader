"""The trader's setups on the channel's alerts, long first (prereg_long_setups_2026-11-10).

    python scripts/long_setups_study.py          # run ONCE: training gate (Holm over 7), held-out for passes
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
from sgt.fast import patterns as pt  # noqa: E402
from sgt.fast.fade import ET, last_entry  # noqa: E402

OUT = Path("reports/long_setups")
SEED, N_BOOT, FAMILY_ALPHA, CONFIRM_ALPHA, LOCATE = 20261110, 10_000, 0.05, 0.05, 0.005
D_STAR = "2026-07-01"
TESTS = ("L4a", "L4b", "L5", "L6", "L7a", "L7b", "S2")
MIN_N = {k: (60 if k.startswith("L7") else 100) for k in TESTS}
HOLDOUT_MIN_N = {k: (15 if k.startswith("L7") else 30) for k in TESTS}   # amendment A1 (red-team F2)
SECONDARY = {"hold15": {"hold": pd.Timedelta(minutes=15)}, "hold60": {"hold": pd.Timedelta(minutes=60)},
             "rr1.5": {"rr": 1.5}, "rr3": {"rr": 3.0}}


def latest(session: str) -> pd.Timestamp:
    """First bar start with no fill: the minute after the last entry minute (15:45 ET; 13:00 on half days)."""
    d = date.fromisoformat(session)
    t = datetime.combine(d, last_entry(d), tzinfo=ET)
    return pd.Timestamp(t).tz_convert("UTC") + pt.BAR


def setups_for(f: pd.DataFrame, t0: pd.Timestamp, halt: bool) -> list[pt.Setup]:
    if halt:
        return pt.first_red(f, t0, "L7")
    return pt.first_red(f, t0) + pt.bull_flag(f, t0) + pt.bos_long(f, t0)


def run(ev: pd.DataFrame, bars: Bars, halt: bool) -> pd.DataFrame:
    rows = []
    for r in ev.itertuples():
        f = bars(r.symbol, r.session)
        if not len(f):
            continue
        cut = latest(r.session)
        for s in setups_for(f, r.t0, halt):
            row = {"symbol": r.symbol, "session": r.session, "kind": s.kind, "side": s.side, "ready": s.ready,
                   "trigger": s.trigger, "stop": s.stop, "cost_rt": r.cost_rt}
            t = pt.simulate(f, s, latest=cut)
            if t is not None:
                fee = r.cost_rt + (LOCATE if s.side == "short" else 0.0)
                row.update(entry_ts=t.entry_ts, entry=t.entry, exit_ts=t.exit_ts, exit=t.exit, reason=t.reason,
                           gross=t.gross, net=t.gross - fee, risk=abs(t.entry - s.stop) / t.entry)
                for k, kw in SECONDARY.items():
                    t2 = pt.simulate(f, s, latest=cut, **kw)
                    row[f"net_{k}"] = None if t2 is None else t2.gross - fee
            rows.append(row)
    return pd.DataFrame(rows)


def boot(d: pd.DataFrame, col: str = "net") -> np.ndarray:
    d = d.dropna(subset=[col])
    u, inv = np.unique(d.session.to_numpy(), return_inverse=True)
    s, n = np.bincount(inv, d[col].to_numpy(float), len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    b = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    return s[b].sum(1) / n[b].sum(1)


def stats(d: pd.DataFrame) -> dict:
    d = d.dropna(subset=["net"])
    if d.empty:
        return {"n": 0}
    m = boot(d)
    return {"n": len(d), "mean": float(d.net.mean()), "median": float(d.net.median()), "win": float((d.net > 0).mean()),
            "p": float((m <= 0).mean()), "lower95": float(np.quantile(m, 0.05)),
            "exits": d.reason.value_counts(normalize=True).round(3).to_dict(), "risk_median": float(d.risk.median())}


def holm(ps: dict[str, float], alpha: float) -> dict[str, bool]:
    order, out, stop = sorted(ps, key=ps.get), {}, False
    for i, k in enumerate(order):
        ok = not stop and ps[k] <= alpha / (len(order) - i)
        stop = stop or not ok
        out[k] = ok
    return out


def main() -> int:
    bars = Bars()
    fe = fade_events().sort_values(["symbol", "session"]).reset_index(drop=True)
    news = pd.concat([pd.read_csv(f"reports/fade_news/{n}_events.csv") for n in ("dev", "holdout")])
    news = news.assign(session=news.session.astype(str))[["symbol", "session", "net", "news_any"]].rename(
        columns={"net": "fade_net"})
    he = halt_events(bars)
    t = pd.concat([run(fe, bars, False), run(he, bars, True)], ignore_index=True)
    t = t.merge(news, on=["symbol", "session"], how="left")
    t["split"] = np.where(t.session < D_STAR, "train", "holdout")
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "trades.csv", index=False)

    train = {k: stats(t[(t.kind == k) & (t.split == "train")]) for k in TESTS}
    # amendment A1 (red-team F1): Holm over all 7 pre-registered tests; an under-powered test keeps its place in the
    # family with p = 1 (it cannot pass), so dropping it never loosens the others' thresholds.
    family = {k: (v["p"] if v.get("n", 0) >= MIN_N[k] else 1.0) for k, v in train.items()}
    passed = holm(family, FAMILY_ALPHA)
    out = {"train": train, "holm_pass": passed, "holdout": {}, "secondary": {}}
    for k in TESTS:
        part = t[t.kind == k]
        n_ev = len(fe) if not k.startswith("L7") else len(he)
        out["secondary"][k] = {"setups": int(len(part)), "filled": int(part.net.notna().sum()), "events": n_ev,
                               **{c: float(part[part.split == "train"][f"net_{c}"].mean()) for c in SECONDARY}}
        if passed.get(k):
            h = stats(part[part.split == "holdout"])
            h["confirmed"] = bool(h.get("n", 0) >= HOLDOUT_MIN_N[k] and h["mean"] > 0 and h["p"] < CONFIRM_ALPHA)
            out["holdout"][k] = h
    for k in TESTS:
        r = train[k]
        print(f"{k:4s} train n{r.get('n', 0):5d} mean {r.get('mean', 0):+.2%} median {r.get('median', 0):+.2%} "
              f"win {r.get('win', 0):.0%} p {r.get('p', 1):.4f} -> {'PASS' if passed.get(k) else 'fail'}"
              + (f" | held-out n{out['holdout'][k]['n']} mean {out['holdout'][k]['mean']:+.2%} "
                 f"{'CONFIRMED' if out['holdout'][k]['confirmed'] else 'not confirmed'}" if k in out["holdout"] else ""))
    (OUT / "dev.json").write_text(json.dumps(out, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="long-setups-2026-11-10-dev", kind="long-setups-dev",
                                  params={"prereg": "docs/findings/prereg_long_setups_2026-11-10.md"}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
