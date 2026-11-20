"""The cost-cap check's arithmetic (prereg_long_cost_cap_2026-11-20), on made-up trades."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_cost_cap_study as st  # noqa: E402


def frame(cheap: float, dear: float, n: int = 40) -> pd.DataFrame:
    rows = []
    for i in range(n):
        s, w = f"2026-07-{1 + i % 28:02d}", (0.01 if i % 2 else -0.01)
        rows.append({"session": s, "net": cheap + w, "gross": cheap + w + 0.008, "cost": 0.008})
        rows.append({"session": s, "net": dear + w, "gross": dear + w + 0.0125, "cost": 0.0125})
    rows.append({"session": "2026-07-01", "net": 0.5, "gross": 0.51, "cost": 0.01})       # at the cap: kept
    return pd.DataFrame(rows)


def test_trades_at_the_cap_are_kept_and_the_difference_is_kept_minus_skipped():
    s = st.stats(frame(-0.005, -0.02))
    assert (s["n_kept"], s["n_skipped"]) == (41, 40)
    assert abs(s["skipped"] + 0.02) < 1e-9 and s["diff"] > 0 and s["p"] < 0.05 and st.passed(s)
    assert abs(s["skipped_gross"] - (-0.02 + 0.0125)) < 1e-9


def test_no_pass_when_the_dear_trades_win_or_do_better_or_are_too_few():
    assert not st.passed(st.stats(frame(0.03, 0.01)))                       # the trades above the cap make money
    assert not st.passed(st.stats(frame(-0.03, -0.01)))                     # they lose less than the kept ones
    few = st.stats(frame(-0.005, -0.02, n=20))
    assert few["n_skipped"] == 20 and not st.passed(few)


def test_only_settled_trades_of_the_live_configuration_and_split_are_read(tmp_path):
    p = tmp_path / "h.csv"
    pd.DataFrame([{"cfg": "new4", "status": "settled", "split": "holdout", "symbol": "NA", "session": "2026-07-01", "net": -0.01, "gross": 0.0, "cost": 0.01},
                  {"cfg": "new4", "status": "settled", "split": "train", "symbol": "B", "session": "2026-01-05", "net": 0.02, "gross": 0.03, "cost": 0.01},
                  {"cfg": "new6", "status": "settled", "split": "holdout", "symbol": "C", "session": "2026-07-01", "net": 0.5, "gross": 0.5, "cost": 0.0},
                  {"cfg": "new4", "status": "none", "split": "holdout", "symbol": "D", "session": "2026-07-01", "net": None, "gross": None, "cost": None}]).to_csv(p, index=False)
    assert st.trades(p, "holdout").symbol.tolist() == ["NA"] and len(st.trades(p, None)) == 2


def test_the_run_is_made_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result.json").write_text("{}")
    assert st.main(["x.csv"]) == 2 and "not repeated" in capsys.readouterr().out
