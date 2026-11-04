"""Execution realism for the live fade book (plan W5, 2026-11-04). Measurement only: nothing here moves a trade.

Per settled trade:
- gap: live estimate (IEX price at the moment of entry / exit) minus the official gross (SIP bars, research code).
  Tells how far the texted P&L can be trusted.
- latency: alert -> live entry. Entry mode from the approval: auto (no reply), approved (OK), armed (no request).
- wait cost: official net at the real entry minus official net at the tested timing (``rule_net``, alert + 10 s).
  Negative = waiting for an approval cost money. Feeds the cost model at the fade decision point (amendment F1).
"""
from __future__ import annotations

import pandas as pd

from sgt.ops.store import OpsStore

AUTO = "auto-entered at expiry (no reply)"            # sgt.fast.fade._ask, the opt-out claim


def frame(db: OpsStore) -> pd.DataFrame:
    with db.tx() as c:
        rows = c.execute("SELECT f.*, a.result AS approval FROM fade_trades f LEFT JOIN approvals a "
                         "ON a.strategy = 'fade:' || f.id AND a.kind='fade' AND a.status='executed' "
                         "WHERE f.status='settled' ORDER BY f.id").fetchall()
    df = pd.DataFrame([dict(r) for r in rows])
    if df.empty:
        return df
    df["mode"] = df.approval.map(lambda a: "armed" if a is None else ("auto" if a == AUTO else "approved"))
    df["est_gross"] = (df.est_entry - df.est_exit) / df.est_entry
    df["gap"] = df.est_gross - df.gross
    df["latency_s"] = (pd.to_datetime(df.est_entry_at, utc=True, format="ISO8601")
                       - pd.to_datetime(df.posted_at, utc=True, format="ISO8601")).dt.total_seconds()
    df["wait_cost"] = df.net - df.rule_net
    return df


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0}
    w = df.wait_cost.dropna()
    return {"n": len(df), "modes": df["mode"].value_counts().to_dict(),
            "gap_mean": float(df.gap.mean()), "gap_abs_median": float(df.gap.abs().median()),
            "latency_median_s": float(df.latency_s.median()), "latency_p90_s": float(df.latency_s.quantile(0.9)),
            "wait_n": len(w), "wait_cost_mean": float(w.mean()) if len(w) else None,
            "net_mean": float(df.net.mean()), "rule_net_mean": float(df.rule_net.mean()) if df.rule_net.notna().any()
            else None}


def _dur(s: float) -> str:
    return f"{int(s // 60)}m{int(s % 60):02d}s"


def lines(db: OpsStore) -> list[str]:
    s = summary(frame(db))
    if not s["n"]:
        return []
    modes = ", ".join(f"{v} {k}" for k, v in sorted(s["modes"].items()))
    line = (f"fade exec: {s['n']} trades ({modes}); texted vs official {s['gap_mean']:+.2%}/trade "
            f"(typical miss {s['gap_abs_median']:.2%}); alert->entry median {_dur(s['latency_median_s'])}")
    if s["wait_cost_mean"] is not None:
        line += f"; waiting cost {s['wait_cost_mean']:+.2%}/trade"
    return [line]
