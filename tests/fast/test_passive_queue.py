from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_passive_queue as pq  # noqa: E402


def test_kept_drops_odd_lots_off_exchange_and_junk():
    prints = [{"p": 1.50, "s": 100, "c": ["@"], "x": "Q"}, {"p": 1.49, "s": 30, "c": ["@", "I"], "x": "Q"},
              {"p": 1.48, "s": 500, "c": ["@"], "x": "D"}, {"p": 1.47, "s": 200, "c": ["Z"], "x": "N"},
              {"p": -1, "s": 100, "c": [], "x": "Q"}, "junk", {"p": 1.46, "s": 300, "c": None, "x": "V"}]
    assert pq.kept(prints) == [(1.50, 100), (1.46, 300)]


def test_queue_units_switch_on_2025_11_03():
    import pandas as pd
    q = pq.queue_shares(pd.Series(["2025-10-31", "2025-11-03", "2026-02-01"]), pd.Series([5, 800, None]))
    assert q.tolist() == [500.0, 800.0, 0.0]
