"""Logic of prereg_fade_repeat_days_2026-11-12 on synthetic events (before the single run)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_repeat_days_study as rd  # noqa: E402


def ev(rows):
    return pd.DataFrame(rows, columns=["symbol", "session", "net", "news_own", "split"])


def test_repeat_is_an_earlier_event_1_to_7_calendar_days_before():
    d = rd.mark_repeats(ev([("A", "2026-03-02", 0.1, 0, "dev"), ("A", "2026-03-09", 0.1, 0, "dev"),
                            ("A", "2026-03-17", 0.1, 0, "dev"), ("B", "2026-03-02", 0.1, 0, "dev"),
                            ("B", "2026-03-03", 0.1, 0, "dev")]))
    got = {(r.symbol, r.session): r.repeat for r in d.itertuples()}
    assert got == {("A", "2026-03-02"): False, ("A", "2026-03-09"): True, ("A", "2026-03-17"): False,
                   ("B", "2026-03-02"): False, ("B", "2026-03-03"): True}
    three = rd.mark_repeats(ev([("A", "2026-03-02", 0.1, 0, "dev"), ("A", "2026-03-06", 0.1, 0, "dev")]), 3)
    assert not three.repeat.any()


def test_stats_and_pass_need_power_p_and_that_skipping_drops_losers():
    rng = np.random.default_rng(2)
    n = 400
    b = pd.DataFrame({"session": [f"2026-0{1 + i % 6}-{1 + i % 28:02d}" for i in range(n)],
                      "net": rng.normal(0.02, 0.05, n), "repeat": np.arange(n) % 3 == 0})
    b.loc[b.repeat, "net"] -= 0.06                         # repeats lose: kept - skipped > 0, skipped total < 0
    st = rd.stats(b)
    assert st["n_skipped"] == int(b.repeat.sum()) and st["diff"] > 0 and rd.train_pass(st)
    assert not rd.train_pass({**st, "p": 0.06}) and not rd.train_pass({**st, "n_skipped": 99})
    assert not rd.train_pass({**st, "skipped_total": 0.5})    # repeats still earn: skipping lowers the total
    assert rd._p(np.array([0.1, np.nan, -0.1, 0.2])) == pytest.approx(0.5)


def test_same_day_is_not_a_repeat_and_a_negative_difference_never_passes():
    d = rd.mark_repeats(ev([("A", "2026-03-02", 0.1, 0, "dev"), ("A", "2026-03-02", 0.1, 0, "dev")]))
    assert not d.repeat.any()
    assert not rd.train_pass({"n_skipped": 300, "diff": -0.01, "p": 0.0, "skipped_total": -5.0})
