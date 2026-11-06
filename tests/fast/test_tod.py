from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_tod_study as tod  # noqa: E402


def test_weight_switches_at_eleven_eastern_including_dst():
    ts = pd.Series(["2026-11-05T15:59:00Z", "2026-11-05T16:00:00Z",      # EST: 10:59 / 11:00
                    "2026-07-01T14:59:00Z", "2026-07-01T15:00:00Z"])     # EDT: 10:59 / 11:00
    assert tod.weight(ts).tolist() == [0.75, 1.5, 0.75, 1.5]


def test_summary_line_reports_mean_size():
    t = pd.DataFrame({"net": [0.02, -0.01], "w": [0.75, 1.5]})
    line = tod.summary_line(t)
    assert "mean size 1.12x" in line and "pre-11 n1 +2.0%" in line and "11+ n1 -1.0%" in line
