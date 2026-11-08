from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_pricesize_study as ps  # noqa: E402


def test_weight_by_entry_price_bucket():
    assert ps.weight(pd.Series([1.0, 1.99, 2.0, 4.99, 5.0, 30.0])).tolist() == [1.5, 1.5, 0.5, 0.5, 1.0, 1.0]


def test_summary_line_names_the_buckets():
    t = pd.DataFrame({"net": [0.03, -0.01], "w": [1.5, 0.5]})
    assert "price sizing" in ps.summary_line(t) and "1.5x n1 +3.0%" in ps.summary_line(t)
