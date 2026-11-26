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
AVG_WIN, AVG_LOSS, STOPPED_NET = 0.134, -0.153, -0.224
# The tested stop-out COUNT: of the 764 held-out alerts, 194 reached the 20% stop (gross at or under -20%). Source: the
# registered stop study, docs/findings/prereg_fade_stop_slippage_2026-11-09.md ("193 / 194 held-out stops"), restated
# in prereg_fade_drift_monitor_2026-11-24.md ("194 of 764 = 25.4%"). The share is DERIVED from the count and its
# population, never typed, and tests/fast/test_fade.py pins both to those documents.
# 2026-11-25 (owner: "derive the displayed stop share from its authoritative reference, show the population"): until
# today this was a typed 0.16 with no source, shown on every short card since 2026-11-04 as "16% stopped". It
# understated how often the stop is reached. A displayed statistic only: no rule, size or decision reads it.
# Archived: STOP_RATE = 0.16
STOPS = 194
STOP_RATE = STOPS / BASE_N
JUDGE_N = 300                                   # forward decision point (amendment F1)
VETO = "veto"                                   # reason of an F5 trade while it can still be exited by NO
VETO_EXIT = "your NO"                           # exit reason after the owner's NO
