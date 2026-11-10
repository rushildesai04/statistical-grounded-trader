"""Setup detectors and fills on 1-minute bars (prereg_long_setups_2026-11-10)."""
from __future__ import annotations

import pandas as pd
import pytest

from sgt.fast import patterns as pt

T0 = pd.Timestamp("2026-11-09 15:00", tz="UTC")


def bars(rows, start=T0, gaps=()):
    """rows: (open, high, low, close, volume); ``gaps``: row indexes preceded by one missing minute."""
    out, t = [], start
    for i, (o, h, lo, c, v) in enumerate(rows):
        if i in gaps:
            t += pt.BAR
        out.append({"ts": t, "open": o, "high": h, "low": lo, "close": c, "volume": v})
        t += pt.BAR
    return pd.DataFrame(out)


POLE = [(10.0, 10.2, 10.0, 10.2, 1000), (10.2, 10.5, 10.2, 10.5, 1200), (10.5, 10.8, 10.5, 10.8, 1500),
        (10.8, 11.0, 10.8, 11.0, 1500)]                         # +10% run-up to 11.00 on heavy volume
FLAG = [(10.95, 10.98, 10.80, 10.85, 400), (10.85, 10.90, 10.70, 10.75, 300)]   # two red, lighter, shallow


def test_first_red_gives_market_and_dip_buy():
    b = bars([(10, 10.5, 10, 10.4, 1), (10.4, 10.6, 10.3, 10.5, 1), (10.5, 10.55, 10.2, 10.3, 1), (10.3, 10.4, 10.1, 10.2, 1)])
    a, s = pt.first_red(b, T0)
    assert (a.kind, a.trigger, a.stop, a.ready) == ("L4a", None, 10.2, T0 + 3 * pt.BAR)
    assert (s.kind, s.trigger, s.stop) == ("L4b", 10.55, 10.2)
    assert pt.first_red(b, T0, "L7")[0].kind == "L7a"
    assert pt.first_red(bars([(1, 2, 1, 2, 1)] * 3), T0) == []                       # no red candle
    late = bars([(1, 2, 1, 2, 1)] * 31 + [(2, 2, 1, 1.5, 1)])
    assert pt.first_red(late, T0) == []                                             # outside WATCH


def test_bull_flag_detected_with_both_orders():
    b = bars(POLE + FLAG + [(10.75, 11.1, 10.75, 11.05, 900)])
    f = pt.find_flag(b, T0 + 4 * pt.BAR)
    assert f["pole_lo"] == 10.0 and f["pole_hi"] == 11.0 and f["flag_bars"] == 2 and f["flag_red"] == 2
    assert f["flag_hi"] == 10.98 and f["flag_lo"] == 10.70 and f["ready"] == T0 + 6 * pt.BAR
    lng, sht = pt.bull_flag(b, T0)
    assert (lng.kind, lng.side, lng.trigger, lng.stop) == ("L5", "long", 10.98, 10.70)
    assert (sht.kind, sht.side, sht.trigger, sht.stop) == ("S2", "short", 10.70, 10.98)


@pytest.mark.parametrize("flag,why", [
    ([(10.95, 11.05, 10.8, 10.85, 400), (10.85, 10.9, 10.7, 10.75, 300)], "new high in the flag"),
    ([(10.95, 10.98, 10.3, 10.4, 400), (10.4, 10.5, 10.35, 10.38, 300)], "retraces more than half the pole"),
    ([(10.95, 10.98, 10.8, 10.85, 4000), (10.85, 10.9, 10.7, 10.75, 3000)], "flag volume not lighter"),
    ([(10.85, 10.98, 10.8, 10.95, 400), (10.85, 10.9, 10.7, 10.75, 300)], "only one red candle"),
])
def test_bull_flag_rejects(flag, why):
    assert pt.find_flag(bars(POLE + flag), T0) is None, why


def test_bull_flag_needs_a_real_pole():
    flat = [(10.0, 10.05, 10.0, 10.05, 1000)] * 3 + [(10.05, 10.2, 10.05, 10.2, 1000)]   # +2% only
    assert pt.find_flag(bars(flat + FLAG), T0) is None


def test_bull_flag_allows_up_to_four_flag_candles():
    four = [(10.95, 10.98, 10.85, 10.96, 400), (10.96, 10.97, 10.8, 10.9, 300), (10.9, 10.95, 10.8, 10.94, 300),
            (10.94, 10.95, 10.7, 10.75, 300)]                   # red count reaches 2 only on the 4th candle
    f = pt.find_flag(bars(POLE + four), T0)
    assert f["flag_bars"] == 4 and f["flag_red"] == 2


def test_bos_long():
    rows = [(10, 10.2, 9.9, 10.1, 1), (10.1, 10.4, 10.0, 10.3, 1), (10.3, 10.8, 10.2, 10.6, 1),   # swing high 10.8
            (10.6, 10.7, 10.4, 10.5, 1), (10.5, 10.6, 10.1, 10.2, 1), (10.2, 10.3, 9.95, 10.1, 1),  # swing low 9.95
            (10.1, 10.5, 10.05, 10.4, 1), (10.4, 10.7, 10.3, 10.65, 1), (10.65, 10.9, 10.6, 10.85, 1)]  # close > 10.8
    (s,) = pt.bos_long(bars(rows), T0)
    assert (s.kind, s.trigger, s.stop) == ("L6", None, 9.95) and s.info["swing_high"] == 10.8
    assert s.ready == T0 + 9 * pt.BAR
    assert pt.bos_long(bars(rows[:8]), T0) == []               # no close above the swing high yet


@pytest.mark.parametrize("finder", [lambda b: pt.first_red(b, T0), lambda b: pt.bull_flag(b, T0 + 4 * pt.BAR),
                                    lambda b: pt.bos_long(b, T0)])
def test_no_peeking(finder):
    """A setup found on the whole day is found identically from only the bars closed before it is ready."""
    # swing high 11.1 (bar 6), swing low 10.3 (bar 8, confirmed at bar 10), close 11.4 > 11.1 on bar 10; then noise
    tail = [(11.2, 11.5, 11.1, 11.4, 1), (11.4, 11.6, 10.9, 11.0, 50_000), (11.0, 12.0, 9.0, 9.5, 60_000),
            (9.5, 13.0, 9.4, 12.5, 70_000), (12.5, 12.6, 8.0, 8.5, 80_000)]
    rows = POLE + FLAG + [(10.75, 11.1, 10.75, 11.05, 900), (10.9, 10.95, 10.4, 10.5, 1),
                          (10.5, 10.6, 10.3, 10.35, 1), (10.35, 11.0, 10.32, 10.95, 1)] + tail
    full = bars(rows)
    found = finder(full)
    assert found, "fixture must contain the setup"
    for s in found:
        cut = full[full.ts < s.ready]
        again = [x for x in finder(cut) if x.kind == s.kind]
        assert again and again[0] == s


def test_simulate_long_market_target_stop_and_time():
    s = pt.Setup("L4a", "long", T0, None, 9.5)
    t = pt.simulate(bars([(10, 10.2, 9.9, 10.1, 1), (10.1, 11.2, 10.0, 11.1, 1)]), s)
    assert (t.entry, t.reason, t.exit) == (10.0, "target", 11.0) and t.gross == pytest.approx(0.10)
    t = pt.simulate(bars([(10, 10.2, 9.4, 9.6, 1)]), s)                     # same bar: stop at the level
    assert (t.reason, t.exit) == ("stop", 9.5)
    t = pt.simulate(bars([(10, 10.2, 9.9, 10.1, 1), (9.3, 9.4, 9.2, 9.3, 1)]), s)   # gap through the stop
    assert (t.reason, t.exit) == ("stop", 9.3)
    t = pt.simulate(bars([(10, 10.3, 9.8, 10.2, 1)] * 40), s)
    assert t.reason == "time" and t.exit_ts == T0 + 29 * pt.BAR and t.gross == pytest.approx(0.02)
    assert pt.simulate(bars([(9.4, 9.6, 9.3, 9.5, 1)]), s) is None          # opens below the stop: no trade


def test_simulate_stop_entry_rules():
    s = pt.Setup("L4b", "long", T0, 10.5, 10.0)
    t = pt.simulate(bars([(10.2, 10.4, 10.1, 10.3, 1), (10.3, 10.6, 10.2, 10.55, 1)]), s)
    assert (t.entry, t.entry_ts) == (10.5, T0 + pt.BAR)
    assert pt.simulate(bars([(10.6, 10.7, 10.5, 10.6, 1)]), s).entry == 10.6           # gap over: open
    t = pt.simulate(bars([(10.2, 10.6, 9.9, 10.0, 1)]), s)                  # trigger and stop in one bar
    assert (t.reason, t.exit) == ("stop", 10.0)
    assert pt.simulate(bars([(10.2, 10.3, 9.95, 10.1, 1), (10.1, 10.6, 10.0, 10.5, 1)]), s) is None  # cancelled
    assert pt.simulate(bars([(10.2, 10.3, 10.1, 10.2, 1)] * 5 + [(10.2, 10.7, 10.2, 10.6, 1)]), s) is None  # expired
    assert pt.simulate(bars([(10.2, 10.4, 10.1, 10.3, 1), (10.3, 10.6, 10.2, 10.55, 1)]), s,
                       latest=T0 + pt.BAR) is None                         # past the last entry minute


def test_simulate_short_mirror():
    s = pt.Setup("S2", "short", T0, 10.0, 10.5)
    t = pt.simulate(bars([(10.2, 10.3, 9.9, 10.0, 1), (10.0, 10.1, 8.9, 9.0, 1)]), s)
    assert (t.entry, t.reason, t.exit) == (10.0, "target", 9.0) and t.gross == pytest.approx(0.10)
    t = pt.simulate(bars([(10.2, 10.3, 9.9, 10.0, 1), (10.6, 10.8, 10.5, 10.7, 1)]), s)
    assert (t.reason, t.exit) == ("stop", 10.6) and t.gross == pytest.approx(-0.06)


@pytest.mark.parametrize("setup", [pt.Setup("L4a", "long", T0, None, 9.5), pt.Setup("L4b", "long", T0, 10.3, 9.5),
                                   pt.Setup("S2", "short", T0, 9.9, 10.6)])
def test_simulate_no_peeking(setup):
    """Red-team F3: the trade is the same when every bar after its exit bar is removed or replaced."""
    rows = [(10.0, 10.2, 9.8, 10.1, 1), (10.1, 10.4, 9.85, 10.3, 1), (10.3, 10.5, 10.0, 10.1, 1),
            (10.1, 10.2, 9.7, 9.8, 1), (9.8, 10.0, 9.6, 9.9, 1)] + [(9.9, 10.1, 9.8, 10.0, 1)] * 40
    full = bars(rows)
    t = pt.simulate(full, setup)
    assert t is not None
    cut = full[full.ts <= t.exit_ts]
    assert pt.simulate(cut, setup) == t
    wild = pd.concat([cut, bars([(50.0, 99.0, 0.01, 1.0, 9)] * 30, start=t.exit_ts + pt.BAR)], ignore_index=True)
    assert pt.simulate(wild, setup) == t
