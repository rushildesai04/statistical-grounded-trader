"""Markdown report renders summary, exit-reason attribution and skip counts."""
from __future__ import annotations

from datetime import date

from sgt.core.types import Segment
from sgt.engine.backtest import run_backtest
from sgt.engine.report import render_report
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.strategy.loader import load_strategy
from sgt.strategy.runtime import EntryOverride


def test_render_report_contains_sections():
    cfg = load_strategy("tests/fixtures/trader_v0_mechanics.yaml")
    sess = default_calendar().previous_sessions(date(2024, 3, 15), 2)
    bars = make_multi_session("AAAA", sess, seed=1, start_price=6.0,
                              segments=(Segment.RTH, Segment.POST),
                              ignitions=(Ignition(start_minute=70, length=15,
                                                  drift_per_bar=0.02, volume_mult=12.0),))
    res = run_backtest(cfg, {"AAAA": bars}, starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), allow_unknown_float=True)
    md = render_report(res)
    assert "## Summary" in md
    assert "## Exit-reason attribution" in md
    assert "## Candidate skip reasons" in md
    assert res.config_hash in md
