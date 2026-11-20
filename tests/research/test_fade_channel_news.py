"""The channel-news test's arithmetic (prereg_fade_channel_news_2026-11-20), on made-up events."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_channel_news_study as st  # noqa: E402


def test_a_news_alert_counts_only_after_the_alert_and_before_the_end_of_the_hold():
    fades = pd.DataFrame([(s, "2026-07-06", "2026-07-06 14:33:08+00:00", "2026-07-06 14:34:00+00:00", 0.01)
                          for s in ("PRE", "HOLD", "BOTH", "EARLY", "LATE", "NONE", "NA")],
                         columns=["symbol", "session", "posted_at", "entry_ts", "net"])
    news = pd.DataFrame([("PRE", "2026-07-06", "2026-07-06 14:33:40+00:00"),       # 32 s after the alert, before the entry bar
                         ("HOLD", "2026-07-06", "2026-07-06 14:34:00+00:00"),      # at the entry bar: during the hold
                         ("BOTH", "2026-07-06", "2026-07-06 14:33:09+00:00"), ("BOTH", "2026-07-06", "2026-07-06 15:00:00+00:00"),
                         ("EARLY", "2026-07-06", "2026-07-06 14:33:08+00:00"),     # not after the alert
                         ("LATE", "2026-07-06", "2026-07-06 15:34:00+00:00"),      # the hold is over
                         ("NONE", "2026-07-07", "2026-07-07 14:33:40+00:00"),      # another session
                         ("NA", "2026-07-06", "2026-07-06 14:50:00+00:00")], columns=["symbol", "session", "posted_at"])
    f = st.flag(fades, news).set_index("symbol")
    assert f.pre.to_dict() == {"PRE": True, "HOLD": False, "BOTH": True, "EARLY": False, "LATE": False, "NONE": False, "NA": False}
    assert f.hold.to_dict() == {"PRE": False, "HOLD": True, "BOTH": False, "EARLY": False, "LATE": False, "NONE": False, "NA": True}
    assert f.flagged.sum() == 4 and len(f) == 7                              # one row a fade, however many news alerts


def frame(flagged: float, rest: float, pre: float | None = None, n: int = 40) -> pd.DataFrame:
    rows = []
    for i in range(n):
        s, w = f"2026-07-{1 + i % 28:02d}", (0.01 if i % 2 else -0.01)
        is_pre = i % 2 == 0
        rows.append({"session": s, "net": (pre if pre is not None and is_pre else flagged) + w, "entry": 1, "flagged": True,
                     "pre": is_pre, "hold": not is_pre})
        rows.append({"session": s, "net": rest + w, "entry": 1, "flagged": False, "pre": False, "hold": False})
    return pd.DataFrame(rows)


def test_a_clear_gap_passes_and_the_counts_are_right():
    s = st.stats(frame(-0.03, 0.02))
    assert (s["n_flagged"], s["n_unflagged"], s["n_pre"], s["n_hold"]) == (40, 40, 20, 20)
    assert abs(s["diff"] + 0.05) < 1e-9 and s["p"] < 0.05 and st.passed(s)


def test_no_pass_without_a_gap_or_with_too_few_or_when_the_entry_part_is_no_worse():
    assert not st.passed(st.stats(frame(0.02, 0.02)))                       # no difference
    assert not st.passed(st.stats(frame(0.05, 0.02)))                       # flagged fades do better
    assert not st.passed(st.stats(frame(-0.03, 0.02, n=20)))                # 20 flagged
    assert not st.passed(st.stats(frame(-0.10, 0.02, pre=0.03)))            # all of it comes after the entry


def test_the_run_is_made_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result.json").write_text("{}")
    assert st.main() == 2 and "not repeated" in capsys.readouterr().out
