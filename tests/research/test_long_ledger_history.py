"""The tested record's ledger, built the way the live ledger is (scripts/build_long_ledger_history.py)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import build_long_ledger_history as b  # noqa: E402


def test_day_is_the_mean_over_marked_names_of_long_nets():
    a = pd.DataFrame({"session": ["2026-01-05", "2026-01-06"], "label": [np.log(1.02), np.log(0.97)], "position": [1, 1]})
    c = pd.DataFrame({"session": ["2026-01-05", "2026-01-06"], "label": [np.log(1.10), np.log(1.10)], "position": [-1, 0]})
    e = pd.DataFrame({"session": ["2026-01-06"], "label": [np.nan], "position": [1]})          # no move known: not a marked name
    d = b.days({"AAA": a, "CCC": c, "EEE": e}, cost=0.0004)
    assert list(d.index) == ["2026-01-05", "2026-01-06"]
    assert d["2026-01-05"] == pytest.approx((0.02 - 0.0004) / 2)       # the short call counts as 0
    assert d["2026-01-06"] == pytest.approx((-0.03 - 0.0004) / 2)      # the flat name counts as 0, the NaN row is no name
