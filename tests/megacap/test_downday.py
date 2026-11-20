"""Down-day long calls: a second forward ledger beside the registered one (prereg_megacap_downday_forward_2026-11-19)."""
from __future__ import annotations

import json
import math

import pytest

from sgt.megacap import downday as dd, longonly as lo

HEAD = "date,symbol,position,ret,void,net\n"
SIX = HEAD + ("2026-11-19,AAA,1,0.10,False,0.0996\n"                                  # before the start: never counted
              "2026-11-20,AAA,1,0.02,False,0.0196\n2026-11-20,BBB,1,-0.03,False,-0.0304\n"
              "2026-11-23,AAA,1,0.01,False,0.0096\n2026-11-23,BBB,-1,-0.02,False,0.0196\n"
              "2026-11-24,AAA,1,0.05,False,0.0496\n")
EXT = HEAD + ("2026-11-20,CCC,1,0.01,False,0.0096\n2026-11-20,DDD,0,0.04,False,0.0\n"
              "2026-11-23,CCC,1,0.03,False,0.0296\n2026-11-23,DDD,1,0.5,True,0.4996\n"   # void row dropped
              "2026-11-24,CCC,0,0.0,False,0.0\n")


def feat(move: float) -> dict:
    """Decision-file features whose stored gap + intraday log moves give ``move`` for the day."""
    lg = math.log1p(move)
    return {"features": {"t_gap": lg / 2, "t_r_intraday": lg / 2}, "position": 1}


def decisions(root, sub: str, day: str, moves: dict) -> None:
    d = root / sub / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.json").write_text(json.dumps({"date": day, "positions": {s: feat(m) for s, m in moves.items()}}))


@pytest.fixture()
def root(tmp_path):
    for sub, body in (("megacap_forward", SIX), ("megacap_ext", EXT)):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "marks.csv").write_text(body)
    decisions(tmp_path, "megacap_forward", "2026-11-20", {"AAA": -0.02, "BBB": 0.01})   # AAA down 2%: a down-day call
    decisions(tmp_path, "megacap_ext", "2026-11-20", {"CCC": -0.01, "DDD": -0.05})       # CCC exactly -1%: not below
    decisions(tmp_path, "megacap_forward", "2026-11-23", {"AAA": 0.03, "BBB": -0.04})    # BBB down but a SHORT call
    decisions(tmp_path, "megacap_ext", "2026-11-23", {"CCC": -0.011, "DDD": -0.03})
    decisions(tmp_path, "megacap_forward", "2026-11-24", {"AAA": -0.03})
    decisions(tmp_path, "megacap_ext", "2026-11-24", {"CCC": 0.0})
    return tmp_path


def test_only_long_calls_on_a_down_day_count_with_the_registered_arithmetic(root):
    r = dd.ledger(root)
    d1, d2, d3 = 0.0196 / 4, 0.0296 / 3, 0.0496 / 2              # AAA; CCC (the void DDD row is dropped); AAA
    assert (r["sessions"], r["since"], r["last"], r["calls"], r["wins"], r["skipped"]) == (3, "2026-11-20", "2026-11-24", 3, 3, 0)
    assert r["total"] == pytest.approx((1 + d1) * (1 + d2) * (1 + d3) - 1)
    assert r["avg_call"] == pytest.approx((0.0196 + 0.0296 + 0.0496) / 3)
    every = [0.0196, -0.0304, 0.0096, 0.0096, 0.0296, 0.0496]    # all long calls on the same sessions
    assert r["all_calls"] == 6 and r["all_avg"] == pytest.approx(sum(every) / 6)
    assert r["all_total"] == pytest.approx(lo.ledger(root, start=dd.START)["total"])      # the registered ledger, same days


def test_nothing_before_the_start_and_the_moves_are_what_the_decision_file_stored(root):
    assert dd.ledger(root, start="2026-11-25") == {"sessions": 0, "skipped": 0, "calls": 0, "wins": 0, "all_calls": 0}
    assert dd.START == "2026-11-20" and dd.DOWN == -0.01 and dd.DECIDE == "2027-04-30"
    m = dd.moves(root / "megacap_forward" / "decisions" / "2026-11-20.json")
    assert m == {"AAA": pytest.approx(-0.02), "BBB": pytest.approx(0.01)}


def test_a_session_whose_inputs_cannot_be_read_is_left_out_whole(root):
    (root / "megacap_ext" / "decisions" / "2026-11-23.json").unlink()                     # one set's decision file is gone
    r = dd.ledger(root)
    assert (r["sessions"], r["skipped"], r["calls"]) == (2, 1, 2) and r["all_calls"] == 4
    decisions(root, "megacap_forward", "2026-11-20", {"BBB": 0.01})                       # AAA is long but has no stored move
    r = dd.ledger(root)
    assert (r["sessions"], r["skipped"], r["calls"], r["since"]) == (1, 2, 1, "2026-11-24")
    p = root / "megacap_forward" / "decisions" / "2026-11-24.json"
    for bad in ("not json", json.dumps({"positions": []}), json.dumps({"positions": {"AAA": {"features": {"t_gap": "x", "t_r_intraday": 0.0}}}}),
                json.dumps({"positions": {"AAA": {"features": {"t_gap": float("nan"), "t_r_intraday": 0.0}}}}), "x" * (dd.MAX_BYTES + 1)):
        p.write_text(bad)
        assert dd.ledger(root)["sessions"] == 0 and dd.ledger(root)["skipped"] == 3
    (root / "megacap_ext" / "marks.csv").unlink()
    assert dd.ledger(root) == {"sessions": 0} and dd.line(root) is None


def test_the_summary_line_and_the_registered_ledger_is_untouched(root):
    before = lo.ledger(root)
    line = dd.line(root)
    assert line.startswith("down-day long calls (record only), 3 sessions since 11-20: ")
    assert "(3 calls, 3 winners, avg +3.29%)" in line and "all long calls same days" in line and "(6 calls, avg +1.46%)" in line
    assert line.endswith("decided 2027-04-30") and lo.ledger(root) == before
    (root / "megacap_ext" / "decisions" / "2026-11-23.json").unlink()
    assert dd.line(root).endswith("; 1 sessions left out (inputs unreadable)")
    assert dd.line(root / "nowhere") is None


def test_a_session_marked_in_one_set_only_and_an_oversized_decision_file_are_not_counted(root):
    with (root / "megacap_forward" / "marks.csv").open("a") as fh:
        fh.write("2026-11-25,AAA,1,0.09,False,0.0896\n")                                  # the other set has no 11-25 marks
    decisions(root, "megacap_forward", "2026-11-25", {"AAA": -0.05})
    r = dd.ledger(root)
    assert (r["sessions"], r["last"], r["calls"]) == (3, "2026-11-24", 3)
    p = root / "megacap_forward" / "decisions" / "2026-11-24.json"
    good = json.loads(p.read_text())
    p.write_text(json.dumps({**good, "pad": "x" * dd.MAX_BYTES}))                         # valid JSON, over the size bound
    assert dd.moves(p) is None and dd.ledger(root)["skipped"] == 1
