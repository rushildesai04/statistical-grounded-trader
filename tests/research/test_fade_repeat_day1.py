"""The day-after repeat test's arithmetic (prereg_fade_repeat_day1_2026-11-20), on made-up events."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_repeat_day1_study as st  # noqa: E402


def events(rows):
    return pd.DataFrame(rows, columns=["symbol", "session", "net"])


def test_gap_is_days_since_the_symbols_previous_event():
    d = st.gaps(events([("AAA", "2026-07-06", 0.1), ("AAA", "2026-07-07", 0.2), ("AAA", "2026-07-10", 0.3),
                        ("BBB", "2026-07-07", 0.4)]))
    got = {(r.symbol, r.session): r.gap for r in d.itertuples()}
    assert got[("AAA", "2026-07-07")] == 1 and got[("AAA", "2026-07-10")] == 3
    assert pd.isna(got[("AAA", "2026-07-06")]) and pd.isna(got[("BBB", "2026-07-07")])


def frame(day_after: float, later: float, n: int = 40) -> pd.DataFrame:
    rows = []
    for i in range(n):
        s = f"2026-07-{1 + i % 28:02d}"
        rows.append({"session": s, "net": day_after + (0.01 if i % 2 else -0.01), "gap": 1})
        rows.append({"session": s, "net": later + (0.01 if i % 2 else -0.01), "gap": 4})
    rows.append({"session": "2026-07-01", "net": 0.5, "gap": 9})            # outside the window: in neither group
    rows.append({"session": "2026-07-02", "net": None, "gap": 1})           # no result: dropped
    return pd.DataFrame(rows)


def test_a_clear_difference_passes_and_counts_only_the_two_groups():
    s = st.stats(frame(0.03, -0.03))
    assert (s["n_day_after"], s["n_later"]) == (40, 40)
    assert abs(s["diff"] - 0.06) < 1e-9 and s["p"] < 0.05 and st.passed(s)


def test_no_pass_when_day_after_repeats_lose_or_are_too_few_or_no_better():
    assert not st.passed(st.stats(frame(-0.01, -0.05)))                     # better than later repeats but still losing
    assert not st.passed(st.stats(frame(0.03, 0.03)))                       # no difference
    few = st.stats(frame(0.03, -0.03, n=20))
    assert few["n_day_after"] == 20 and not st.passed(few)


def test_the_run_is_made_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result.json").write_text("{}")
    assert st.main() == 2 and "not repeated" in capsys.readouterr().out
