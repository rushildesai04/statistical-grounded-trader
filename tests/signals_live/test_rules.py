"""Decision rules for live signal paper trading."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sgt.signals_live.ideas import OptionIdea, StockIdea, parse_idea
from sgt.signals_live.rules import AlertPlan, IdeaPlan, Limits, Skip, State, decide_alert, decide_idea

T = datetime(2026, 10, 29, 14, 31, 55, tzinfo=timezone.utc)  # 10:31:55 ET


def test_parse_stock_and_option_ideas():
    s = parse_idea("🚨 STOCKS\n\n$PCLA - HIGH RISK\n\nIdeal Entry: 8.36\n\nPT: 5% , 10% , 20%+\n\nSL: 7.02\n\n-Trade at your own risk")
    assert s == StockIdea("PCLA", 8.36, (0.05, 0.10, 0.20), 7.02, True)
    o = parse_idea("🚨 QQQ PUTS - 0DTE\n\nStrike : 712 @ 2.65\n\nTrim @ 20% - 100%+\n\nSL : 50%")
    assert o == OptionIdea("QQQ", "P", 0, 712.0, 2.65, 0.5)
    assert parse_idea("$LRHC\nIdeal Entry: 4.95\nPT: 5%\nSL: 5.21") is None      # stop above entry
    assert parse_idea("PCLA\n\n+30%\n\n-trim more here") is None


def test_alert_enters_next_minute_after_reaction():
    p = decide_alert("ABCD", "volume_spike", T, T + timedelta(seconds=2), 4.0, State())
    assert isinstance(p, AlertPlan)
    assert p.submit_at == datetime(2026, 10, 29, 14, 33, tzinfo=timezone.utc)
    assert p.qty == 500 and p.hold == timedelta(minutes=15)


@pytest.mark.parametrize("kw,reason", [
    (dict(alert_type="halted"), "not traded"),
    (dict(now=T + timedelta(seconds=90)), "stale"),
    (dict(last_price=0.8), "outside $1"),
    (dict(posted=T.replace(hour=13, minute=20)), "entry window"),  # 09:22 ET
    (dict(posted=T.replace(hour=19, minute=50)), "entry window"),
])
def test_alert_skips(kw, reason):
    args = dict(symbol="ABCD", alert_type="volume_spike", posted=T, now=T, last_price=4.0, state=State()) | kw
    if "posted" in kw and "now" not in kw:
        args["now"] = kw["posted"]
    r = decide_alert(**args)
    assert isinstance(r, Skip) and reason in r.reason


def test_state_guards():
    st = State(paused=True, pause_reason="kill")
    assert "paused" in decide_alert("ABCD", "news", T, T, 4.0, st).reason
    st = State(open_symbols={"ABCD"})
    assert "already holding" in decide_alert("ABCD", "news", T, T, 4.0, st).reason
    st = State(open_symbols={f"S{i}" for i in range(5)})
    assert "max open" in decide_alert("ABCD", "news", T, T, 4.0, st).reason
    st = State()
    st.roll(T)
    st.done_today.add(("alert", "news", "ABCD"))
    assert "already traded" in decide_alert("ABCD", "news", T, T, 4.0, st).reason
    st.roll(T + timedelta(days=1))  # new day clears the dedup
    assert isinstance(decide_alert("ABCD", "news", T + timedelta(days=1), T + timedelta(days=1), 4.0, st), AlertPlan)


def test_idea_plan_three_brackets():
    idea = StockIdea("PCLA", 8.36, (0.05, 0.10, 0.20), 7.02, False)
    p = decide_idea(idea, T, T, 8.30, State())
    assert isinstance(p, IdeaPlan)
    assert p.limit == 8.40                       # 8.36 * 1.005
    assert sum(l.qty for l in p.legs) == 238     # floor(2000 / 8.40)
    assert [l.take_profit for l in p.legs] == [8.78, 9.2, 10.03]
    assert {l.stop for l in p.legs} == {7.02}


def test_idea_skips_runaway_and_window():
    idea = StockIdea("PCLA", 8.36, (0.05,), 7.02, False)
    assert "already ran" in decide_idea(idea, T, T, 8.70, State()).reason
    late = T.replace(hour=20, minute=0)
    assert "idea window" in decide_idea(idea, late, late, 8.30, State()).reason
