"""BoS + EMA study plumbing (prereg_bos_ema_2026-11-13): gating, the t0 / 15:30 window, the pivot-low stop."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import bos_ema_study as bos  # noqa: E402
from sgt.fast import structure as st  # noqa: E402

S = "2026-11-12"


def frame(n=120, start="2026-11-12 15:00"):            # 10:00 ET; a saw-tooth uptrend that keeps making pivots
    k = np.arange(n)
    base = 5 + 0.02 * k + 0.25 * np.sin(k / 3.0)
    return pd.DataFrame({"ts": pd.date_range(start, periods=n, freq="1min", tz="UTC"), "open": base, "high": base + 0.05,
                         "low": base - 0.05, "close": base + 0.02, "volume": 1000.0})


def test_signals_need_a_stop_and_respect_t0_and_the_cutoff():
    f = frame()
    raw = st.bullish_breaks(f)
    assert len(raw) >= 5
    got = bos.signals(f, f.ts.iloc[40], S)
    assert got and all(s.i >= 40 and s.flag_low is not None for _, _, s in got)
    assert {s.i for _, _, s in got} == {b.i for b in raw if b.i >= 40 and b.stop is not None}
    late = frame(start="2026-11-12 20:00")              # 15:00 ET: bars closing after 15:30 never signal
    assert all(late.ts.iloc[s.i] + bos.bfs.BAR <= bos.bfs.et_bar(S, 15, 30) for _, _, s in bos.signals(late, late.ts.iloc[0], S))


def test_trades_enter_at_the_next_open_with_the_pivot_low_stop():
    f = frame()
    ev = pd.DataFrame([{"symbol": "X", "session": S, "t0": f.ts.iloc[30], "cost_rt": 0.01, "halt": False}])
    rows, green = bos.event_rows(ev, lambda sym, ses: f)
    assert rows and green
    for r in rows:
        i = r["signal_i"]
        assert r["entry_ts"] == f.ts.iloc[i + 1] and r["entry"] == float(f.open.iloc[i + 1]) and r["flag_low"] < r["entry"]
        assert abs(r["net"] - (r["gross"] - 0.01)) < 1e-12
    by = pd.DataFrame(rows)
    one = by[by.candidate == "S1X2"].sort_values("entry_ts")
    assert (one.entry_ts.iloc[1:].to_numpy() >= one.exit_ts.iloc[:-1].to_numpy()).all()      # one position at a time


def test_next_bar_comparison_is_signal_vs_all_gated_bars():
    g = pd.DataFrame({"session": ["a"] * 4 + ["b"] * 4, "is_signal": [True, False, False, False] * 2,
                      "green": [1.0, 0, 0, 0, 1.0, 0, 0, 0]})
    d = bos.diff_p(g, "green")
    assert d["signal"] == 1.0 and d["all_gated"] == 0.25 and d["p"] == 0.0
