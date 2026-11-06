"""Size the fade by its confidence score, forward only (prereg_fade_confsize_2026-11-06).

    python scripts/fade_confsize_study.py shadow   # nightly (runner, after the forward record): forward events
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.fast.fade_texts import confidence

OUT = Path("reports/fade_confsize")
HIGH, W_HIGH, W_LOW = 90, 1.5, 0.5


def weight(conf: int) -> float:
    return W_HIGH if conf >= HIGH else W_LOW


def conf_of(alert_type: str, n_own: float, n_earn: float, at: pd.Timestamp) -> int:
    """Confidence v2 at the alert from the forward news flags (NaN = lookup failed = unknown)."""
    ctx = {} if pd.isna(n_own) else {"news": {"news_own": bool(n_own), "news_earn": bool(n_earn == 1)}}
    return confidence(alert_type, ctx, at.to_pydatetime())[0]


def summary_line(t: pd.DataFrame) -> str:
    by = "  ".join(f"{w}x n{len(g)} {g.net.mean():+.1%}" for w, g in t.groupby("w"))
    return (f"fade confidence sizing fwd n={len(t)}: equal {t.net.mean():+.2%}/trade, sized {(t.net * t.w).mean():+.2%}"
            f"/trade (mean size {t.w.mean():.2f}x); {by}")


def cmd_shadow() -> int:
    from alert_fade_forward import CHANNEL, DB, forward_events
    from alert_fade_test import score
    from fade_brain_study import forward_news
    from fade_challengers import ET
    from megacap_forward import last_complete_session
    from sgt.intraday.store import ParquetBarStore
    from telegram_alert_test import load_alerts

    store = ParquetBarStore("data/alpaca")
    end = last_complete_session(datetime.now(ET))
    ev = forward_events(load_alerts(DB, CHANNEL), store, end, fill_bars=False).reset_index(drop=True)
    ev = ev[ev.status == "ok"].reset_index(drop=True) if "status" in ev else ev
    if ev.empty:
        print("no forward events yet")
        return 0
    ev["net"] = score(ev, store).net.to_numpy()
    ev = ev.dropna(subset=["net"]).reset_index(drop=True)
    ev["posted_at"] = pd.to_datetime(ev.posted_at, utc=True)
    nf = forward_news(ev)
    conf = [conf_of(a, o, e, pd.Timestamp(ts)) for a, o, e, ts in zip(ev.alert_type, nf.n_own, nf.n_earn, ev.entry_ts)]
    t = ev[["symbol", "session", "entry_ts", "net"]].assign(conf=conf, w=[weight(c) for c in conf])
    t["session"] = t.session.astype(str)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "shadow.csv", index=False)
    line = summary_line(t)
    (OUT / "shadow_summary.json").write_text(json.dumps({"asof": str(end), "n": len(t), "line": line}, indent=2))
    print(line)
    return 0 if np.isfinite(t.net).all() else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["shadow"])
    return {"shadow": cmd_shadow}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
