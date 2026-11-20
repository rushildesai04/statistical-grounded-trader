"""The long-while-short check's arithmetic (prereg_long_while_short_2026-11-20), on made-up trades."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_while_short_study as st  # noqa: E402


def test_a_long_is_flagged_only_on_a_taken_alert_inside_the_shorts_hour():
    al = pd.DataFrame([("IN", "2026-07-06", True, "2026-07-06 14:34:00+00:00"), ("LATE", "2026-07-06", True, "2026-07-06 14:34:00+00:00"),
                       ("EARLY", "2026-07-06", True, "2026-07-06 14:34:00+00:00"), ("NEWS", "2026-07-06", False, "2026-07-06 14:34:00+00:00"),
                       ("NA", "2026-07-06", True, "2026-07-06 14:34:00+00:00")], columns=["symbol", "session", "taken", "short_ts"])
    tr = pd.DataFrame([("IN", "2026-07-06", "2026-07-06T14:55:00+00:00"), ("LATE", "2026-07-06", "2026-07-06T15:34:00+00:00"),
                       ("EARLY", "2026-07-06", "2026-07-06T14:33:00+00:00"), ("NEWS", "2026-07-06", "2026-07-06T14:55:00+00:00"),
                       ("NA", "2026-07-06", "2026-07-06T14:34:00+00:00"), ("NOALERT", "2026-07-06", "2026-07-06T14:55:00+00:00")],
                      columns=["symbol", "session", "entry_ts"])
    f = st.flag(tr, al).set_index("symbol").short_open.to_dict()
    assert f == {"IN": True, "LATE": False, "EARLY": False, "NEWS": False, "NA": True, "NOALERT": False}


def frame(flagged: float, rest: float, n: int = 40) -> pd.DataFrame:
    rows = []
    for i in range(n):
        s, w = f"2026-07-{1 + i % 28:02d}", (0.01 if i % 2 else -0.01)
        rows += [{"session": s, "net": flagged + w, "short_open": True}, {"session": s, "net": rest + w, "short_open": False}]
    return pd.DataFrame(rows)


def test_pass_needs_enough_losing_flagged_longs_that_do_worse_than_the_rest():
    s = st.stats(frame(-0.02, -0.005))
    assert (s["n_flagged"], s["n_rest"]) == (40, 40) and abs(s["diff"] - 0.015) < 1e-9 and s["p"] < 0.05 and st.passed(s)
    assert not st.passed(st.stats(frame(0.01, 0.03)))                       # the flagged longs make money
    assert not st.passed(st.stats(frame(-0.01, -0.03)))                     # they lose less than the rest
    assert not st.passed(st.stats(frame(-0.02, -0.005, n=20)))              # too few


def test_only_settled_trades_of_the_live_configuration_and_split_are_read(tmp_path):
    p = tmp_path / "h.csv"
    pd.DataFrame([{"cfg": "new4", "status": "settled", "split": "holdout", "symbol": "NA", "session": "2026-07-01", "net": -0.01, "entry_ts": "x"},
                  {"cfg": "new4", "status": "settled", "split": "train", "symbol": "B", "session": "2026-01-05", "net": 0.02, "entry_ts": "x"},
                  {"cfg": "new6", "status": "settled", "split": "holdout", "symbol": "C", "session": "2026-07-01", "net": 0.5, "entry_ts": "x"},
                  {"cfg": "new4", "status": "none", "split": "holdout", "symbol": "D", "session": "2026-07-01", "net": None, "entry_ts": None}]).to_csv(p, index=False)
    assert st.trades(p, "holdout").symbol.tolist() == ["NA"]


def test_the_run_is_made_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result.json").write_text("{}")
    assert st.main(["x.csv"]) == 2 and "not repeated" in capsys.readouterr().out
