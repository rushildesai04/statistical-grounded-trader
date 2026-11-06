from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.ops import model_tape as mt


def oos(positions, labels):
    n = len(labels)
    return pd.DataFrame({"session": pd.bdate_range("2025-01-02", periods=n).strftime("%Y-%m-%d"),
                         "label": labels, "pred": [0.001 * p for p in positions], "position": positions})


def test_stats_charge_costs_only_when_in_the_market_and_compare_to_holding():
    s = mt.stats(oos([1, -1, 0, 1], [0.01, -0.02, 0.05, -0.01]))
    assert s["per_trade_bp"] == pytest.approx(((0.01 - 4e-4) + (0.02 - 4e-4) + (-0.01 - 4e-4)) / 3 * 1e4)
    assert s["hit"] == pytest.approx(2 / 3) and s["traded_share"] == 0.75
    assert s["hold_ann"] == pytest.approx(sum([0.01, -0.02, 0.05, -0.01]) / 4 * 252)


def test_view_and_render_are_json_safe():
    o = oos([1, 0, -1], [0.01, 0.0, float("nan")])
    d = pd.DataFrame({"date": pd.bdate_range("2025-01-02", periods=3), "open": 1.0, "high": 1.1, "low": 0.9,
                      "close": 1.0})
    v = mt.view("</script>X", "g", o, d)
    assert [m["pos"] for m in v["marks"]] == [1] and v["last"]["position"] == 0
    html = mt.render([v], datetime(2026, 11, 6, tzinfo=timezone.utc), [{"date": "2026-11-05", "symbol": "NVDA",
                                                                       "position": 1, "ret": 0.035, "net": 0.0353}])
    assert "</script>X" not in html and "/*MODELS*/null" not in html and '"forward": [{"date": "2026-11-05"' in html
