"""Plumbing of prereg_ema_exact_cross_2026-11-18 on synthetic bars (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ema_exact_cross_study as st  # noqa: E402


def bars(closes, start="2026-01-05 14:30", opens=None, highs=None, volume=None, skip=()):
    """Regular-hours 1-minute bars (UTC); ``skip`` removes minutes (a thin stock)."""
    n = len(closes)
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    c = np.asarray(closes, float)
    o = np.asarray(opens, float) if opens is not None else np.r_[c[0], c[:-1]]
    h = np.asarray(highs, float) if highs is not None else np.maximum(o, c)
    f = pd.DataFrame({"ts": ts, "open": o, "high": h, "low": np.minimum(o, c), "close": c,
                      "volume": np.asarray(volume, float) if volume is not None else np.full(n, 1000.0), "segment": "rth"})
    return f.drop(index=list(skip)).reset_index(drop=True)


def test_cross_price_is_where_the_two_live_emas_meet():
    e9, e21 = 9.80, 10.00
    p = st.cross_price(e9, e21)
    live9, live21 = e9 + st.A9 * (p - e9), e21 + st.A21 * (p - e21)
    assert live9 == pytest.approx(live21)
    assert e9 + st.A9 * (p + 0.01 - e9) > e21 + st.A21 * (p + 0.01 - e21)
    assert e9 + st.A9 * (p - 0.01 - e9) < e21 + st.A21 * (p - 0.01 - e21)


def at(t, f, i=30):
    """The event of minute ``i`` of ``f`` (the seed minute, where both EMAs are equal, can give an earlier one)."""
    return t[t.ts == f.ts[i]].iloc[0]


def falling_then_jump(jump_close, jump_high=None, n_after=40):
    down = list(np.linspace(10.0, 9.0, 30))                    # EMA 9 below EMA 21
    closes = down + [jump_close] + [jump_close] * n_after
    highs = None
    if jump_high is not None:
        o = np.r_[closes[0], closes[:-1]]
        highs = np.maximum(o, closes); highs[30] = jump_high
    return bars(closes, highs=highs)


def test_exact_entry_is_the_cross_price_and_later_entries_are_later_opens():
    f = falling_then_jump(13.0)
    t = st.day_events(f)
    first = at(t, f)
    c = pd.Series(f.close)
    e9, e21 = c.ewm(span=9, adjust=False).mean(), c.ewm(span=21, adjust=False).mean()
    p = st.cross_price(e9[29], e21[29])
    assert first.ts == f.ts[30] and first.pstar == pytest.approx(p) and 9.0 < p < 13.0
    assert first.p_EXACT == pytest.approx(p)                   # the minute opened below the cross price
    assert not first.fake
    assert first.p_NEXT == f.open[31] and first.p_PLUS1 == f.open[32] and first.p_PLUS5 == f.open[36]
    assert first.g_EXACT_5 == pytest.approx(f.close[35] / p - 1)
    assert first.g_NEXT_15 == pytest.approx(f.close[45] / f.open[31] - 1)
    assert "g_PLUS5_5" not in t.columns and "g_PLUS5_15" in t.columns


def test_a_gap_over_the_cross_price_is_bought_at_the_open():
    f = falling_then_jump(13.0)
    f.loc[30, "open"] = 12.5                                   # the first print of the minute is already above
    f.loc[30, "high"] = 13.0
    assert at(st.day_events(f), f).p_EXACT == pytest.approx(12.5)


def test_a_cross_that_fails_by_the_close_is_a_fake_out():
    f = falling_then_jump(9.0, jump_high=13.0)                 # spikes over the cross price, closes back below
    first = at(st.day_events(f), f)
    assert first.fake


def test_no_event_without_room_for_every_entry():
    f = falling_then_jump(13.0, n_after=5)                     # the session ends before minute i + 6
    t = st.day_events(f)
    assert t.empty or not (t.ts == f.ts[30]).any()


def test_missing_minutes_use_the_first_bar_after_and_drop_marks_that_come_before_an_entry():
    f = falling_then_jump(13.0)
    g = f.drop(index=range(31, 40)).reset_index(drop=True)     # no trade for 9 minutes after the cross
    first = at(st.day_events(g), f)
    assert first.p_NEXT == f.open[40] and first.p_PLUS5 == f.open[40]
    assert not first.ok_5 and np.isnan(first.g_EXACT_5)        # NEXT comes after the 5-minute mark: dropped for all
    assert first.ok_15 and first.g_EXACT_15 == pytest.approx(f.close[45] / first.pstar - 1)


def test_volume_rules():
    v = np.full(71, 1000.0)
    v[29] = 2500.0                                             # the minute before the cross: rising
    f = bars(falling_then_jump(13.0).close, volume=v)
    e = at(st.day_events(f), f)
    assert e.RISING and not e.SPIKE
    v = np.full(71, 1000.0)
    v[30] = 3500.0                                             # the cross minute itself: spike (look-ahead)
    f = bars(falling_then_jump(13.0).close, volume=v)
    e = at(st.day_events(f), f)
    assert e.SPIKE and not e.RISING


def frame(nets, sessions, rising=True):
    n = len(nets)
    d = {"session": sessions, "symbol": "AAA", "minute": 600, "fake": False, "RISING": rising, "SPIKE": False, "p_EXACT": 10.0}
    for name in st.ENTRIES:
        d[f"p_{name}"], d[f"cost_{name}"] = 10.0, 0.0
        for m in st.MARKS:
            if st.scored(name, m):
                d[f"g_{name}_{m}"] = nets
    return pd.DataFrame(d, index=range(n))


def test_gate_passes_only_with_a_lower_bound_above_zero():
    rng = np.random.default_rng(1)
    sessions = [f"2026-01-{d:02d}" for d in rng.integers(1, 29, 600)]
    good = st.evaluate(frame(rng.normal(0.01, 0.01, 600), sessions))
    assert good["claim_a"]["pass"] and all(b > 0 for b in good["claim_a"]["lower_bounds_net"].values())
    flat = st.evaluate(frame(rng.normal(0.0, 0.01, 600), sessions))
    assert not flat["claim_a"]["pass"]


def test_gate_fails_closed_when_no_rising_event_exists():
    res = st.evaluate(frame(np.full(50, 0.05), ["2026-01-05"] * 25 + ["2026-01-06"] * 25, rising=False))
    assert res["claim_a"]["pass"] is False and res["claim_a"]["lower_bounds_net"]["5"] is None


def test_costs_come_off_the_gross_and_delay_is_the_price_ratio():
    t = frame(np.full(40, 0.01), ["2026-01-05"] * 20 + ["2026-01-06"] * 20)
    t["cost_EXACT"], t["p_NEXT"] = 0.007, 10.05
    r = st.evaluate(t)["rules"]["RISING"]
    assert r["entries"]["EXACT"]["5"]["net"] == pytest.approx(0.003)
    assert r["delay"]["NEXT"]["mean"] == pytest.approx(0.005)


def test_a_second_run_is_refused(tmp_path, capsys):
    (tmp_path / "result_train.json").write_text("{}")
    assert st.main(["--before", "2026-04-01", "--out", str(tmp_path)]) == 2
    assert "not repeated" in capsys.readouterr().out


def test_the_closed_period_cannot_be_opened(tmp_path):
    assert st.main(["--before", "2026-07-01", "--out", str(tmp_path)]) == 2
