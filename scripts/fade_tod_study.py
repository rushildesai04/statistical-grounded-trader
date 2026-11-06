"""Time-of-day sizing for the fade, forward only (prereg_fade_tod_2026-11-06).

    python scripts/fade_tod_study.py shadow   # nightly (runner, after the forward record): forward events
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("reports/fade_tod")
CUT_MIN, W_EARLY, W_LATE = 11 * 60, 0.75, 1.5


def weight(entry_ts: pd.Series) -> np.ndarray:
    """0.75x for entries before 11:00 ET, 1.5x from 11:00 on."""
    et = pd.to_datetime(entry_ts, utc=True).dt.tz_convert("America/New_York")
    return np.where(et.dt.hour * 60 + et.dt.minute < CUT_MIN, W_EARLY, W_LATE)


def summary_line(t: pd.DataFrame) -> str:
    by = "  ".join(f"{'pre-11' if w == W_EARLY else '11+'} n{len(g)} {g.net.mean():+.1%}" for w, g in t.groupby("w"))
    return (f"fade time-of-day fwd n={len(t)}: equal {t.net.mean():+.2%}/trade, sized {(t.net * t.w).mean():+.2%}/trade "
            f"(mean size {t.w.mean():.2f}x); {by}")


def cmd_shadow() -> int:
    from alert_fade_forward import CHANNEL, DB, forward_events
    from alert_fade_test import score
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
    t = ev.dropna(subset=["net"])[["symbol", "session", "entry_ts", "net"]].reset_index(drop=True)
    t["w"] = weight(t.entry_ts)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_csv(OUT / "shadow.csv", index=False)
    line = summary_line(t)
    (OUT / "shadow_summary.json").write_text(json.dumps({"asof": str(end), "n": len(t), "line": line}, indent=2))
    print(line)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["shadow"])
    return {"shadow": cmd_shadow}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
