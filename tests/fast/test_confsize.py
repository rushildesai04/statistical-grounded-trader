from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_confsize_study as cs  # noqa: E402


def test_weight_follows_confidence_v2_cells():
    morning = pd.Timestamp("2026-11-06T14:40:00Z")                         # 09:40 ET
    assert cs.conf_of("news", 1.0, 0.0, morning) == 38 and cs.weight(38) == 0.5        # RAYA
    assert cs.conf_of("volume_spike", 0.0, 0.0, morning) == 98 and cs.weight(98) == 1.5
    assert cs.conf_of("volume_spike", np.nan, np.nan, morning) == 93                    # unknown news
    assert cs.conf_of("news", 0.0, 0.0, pd.Timestamp("2026-11-06T19:30:00Z")) == 66     # news alert, after 14:00


def test_summary_reports_mean_size():
    t = pd.DataFrame({"net": [0.03, -0.02], "w": [1.5, 0.5]})
    assert "mean size 1.00x" in cs.summary_line(t) and "0.5x n1 -2.0%" in cs.summary_line(t)
