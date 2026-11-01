"""Forward paper record of the MetaTradings alert fade (no orders, no capital).

Rules: docs/findings/prereg_alert_fade_2026-11-01.md, amendment F1. Pulls new channel history
(needs the local Telegram session), fills 1-minute bars, rebuilds the forward events with the alert
test's own code and scores them with the held-out scorer. Re-running is idempotent.

Usage:
    python scripts/alert_fade_forward.py              # pull channel + bars, score, print
    python scripts/alert_fade_forward.py --no-pull    # score what is already stored
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from alert_fade_test import score
from backfill_alpaca import backfill
from megacap_forward import last_complete_session
from sgt.intraday.sources import AlpacaHistoricalBars
from sgt.intraday.store import ParquetBarStore
from telegram_alert_test import attach_trades, boot_means, load_alerts, primary_events

ET = ZoneInfo("America/New_York")
CHANNEL = "-1001254273411"
FORWARD_START = date(2026, 10, 30)
RUN_ID = "alert-fade-forward-2026-10-30"
DB = "sgt.db"
OUT = Path("reports/alert_fade")
DECIDE_N, EXTEND_N = 300, 600
SEED = 20261101


def pull_channel() -> None:
    """Fetch new messages (resumes from the max stored id); failures propagate."""
    subprocess.run([sys.executable, "scripts/telegram_backfill.py", "--since", f"{FORWARD_START}",
                    "--channels", CHANNEL], check=True, timeout=1800)


def forward_events(alerts: pd.DataFrame, store: ParquetBarStore, end: date, fill_bars: bool) -> pd.DataFrame:
    a = alerts[(alerts.session >= FORWARD_START) & (alerts.session <= end)]
    if fill_bars:
        fetch = AlpacaHistoricalBars(feed="sip").fetch
        for d, g in a.groupby("session"):
            backfill(sorted(g.symbol.unique()), d, d, store=store, fetch=fetch)
    ev = primary_events(attach_trades(a, store)).drop_duplicates(["symbol", "session"])
    return ev.assign(session=ev.session.astype(str))


def month_summaries(t: pd.DataFrame, today: date) -> dict[str, dict]:
    """Cumulative forward metrics at the end of each finished calendar month."""
    out = {}
    months = pd.to_datetime(t.session).dt.to_period("M")
    for m in sorted(months.unique()):
        if m.end_time.date() >= today:
            continue
        x = t[months <= m].net
        out[str(m)] = {"n": int(len(x)), "mean": float(x.mean()), "win": float((x > 0).mean()),
                       "through": str(t[months <= m].session.max())}
    return out


def decision(t: pd.DataFrame, rng: np.random.Generator) -> str:
    """Amendment F1 rule, applied only at a month-end with enough trades."""
    n, mean = len(t), float(t.net.mean()) if len(t) else 0.0
    if n < DECIDE_N:
        return f"collecting ({n}/{DECIDE_N} trades)"
    if mean <= 0:
        return "DROP (mean net <= 0)"
    lb = float(np.quantile(boot_means(t.net.to_numpy(float), t.session.to_numpy(), rng), 0.05))
    if lb > 0:
        return f"KEEP (lower95 {lb:+.2%})"
    return f"continue to {EXTEND_N} (lower95 {lb:+.2%})" if n < EXTEND_N else "KEEP (mean net > 0 at n >= 600)"


def record(months: dict[str, dict]) -> None:
    from sgt.db.session import get_session, init_db
    from sgt.decision import trial_ledger

    if not months:
        return
    init_db()
    with get_session() as s:
        for m, metrics in months.items():   # idempotent on (run_id, params)
            trial_ledger.record_trial(s, run_id=RUN_ID, kind="alert-fade-forward",
                                      params={"prereg": "docs/findings/prereg_alert_fade_2026-11-01.md#F1",
                                              "month": m}, metrics=metrics)
    print("trial ledger: recorded months", ", ".join(months))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-pull", action="store_true", help="skip Telegram and bar refresh")
    args = ap.parse_args()
    now = datetime.now(ET)
    end = last_complete_session(now)
    print(f"--- {now:%Y-%m-%d %H:%M} ET alert-fade forward, sessions {FORWARD_START} .. {end}")
    if not args.no_pull:
        pull_channel()
    store = ParquetBarStore("data/alpaca")
    ev = forward_events(load_alerts(DB, CHANNEL), store, end, fill_bars=not args.no_pull)
    t = score(ev, store).dropna(subset=["gross"])
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "forward.csv", index=False)
    if t.empty:
        print("no forward trades yet")
        return 0
    print(f"forward trades {len(t)} ({t.session.min()} .. {t.session.max()}), {len(ev) - len(t)} without bars")
    print(f"mean net {t.net.mean():+.2%}  median {t.net.median():+.2%}  win {(t.net > 0).mean():.0%}  "
          f"worst {t.net.min():+.1%}  gross {t.gross.mean():+.2%}")
    months = month_summaries(t, now.date())
    for m, v in months.items():
        print(f"  {m}: n={v['n']} cumulative mean {v['mean']:+.2%}")
    print("status:", decision(t, np.random.default_rng(SEED)))
    (OUT / "forward_summary.json").write_text(json.dumps({"asof": str(end), "months": months}, indent=2))
    record(months)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
