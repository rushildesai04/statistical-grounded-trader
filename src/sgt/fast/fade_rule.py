"""The fade's frozen rule constants and its tested record (prereg_alert_fade_2026-11-01), shared by the live book
(sgt.fast.fade) and its texts (sgt.fast.fade_texts). Split from fade.py 2026-11-06 (file size); values unchanged."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
STOP, HOLD = 0.20, timedelta(minutes=60)
# The frozen rule on held-out data it never saw (2026-07..10, 764 trades, reports/alert_fade/trades.csv), shown on
# every request so each decision is made against the real record. Update at the F1 decision point.
BASE_N, BASE_WIN, BASE_WIN_CI, BASE_EV = 764, 0.573, (0.54, 0.61), 0.0112
AVG_WIN, AVG_LOSS, STOP_RATE, STOPPED_NET = 0.134, -0.153, 0.16, -0.224
JUDGE_N = 300                                   # forward decision point (amendment F1)
VETO = "veto"                                   # reason of an F5 trade while it can still be exited by NO
VETO_EXIT = "your NO"                           # exit reason after the owner's NO
