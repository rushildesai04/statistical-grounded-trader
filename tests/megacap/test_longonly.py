"""Long-calls-only forward ledger (prereg_megacap_long_forward_2026-11-13)."""
from __future__ import annotations

import pytest

from sgt.megacap import longonly as lo

HEAD = "date,symbol,position,ret,void,net\n"
SIX = HEAD + ("2026-11-13,AAA,1,0.10,False,0.0996\n"              # before the start: never counted
              "2026-11-16,AAA,1,0.02,False,0.0196\n2026-11-16,BBB,-1,-0.03,False,0.0296\n"
              "2026-11-17,AAA,0,0.01,False,0.0\n2026-11-17,BBB,1,-0.02,False,-0.0204\n"
              "2026-11-18,AAA,1,0.05,False,0.0496\n")              # not marked in the other set: not counted
EXT = HEAD + ("2026-11-16,CCC,1,0.01,False,0.0096\n2026-11-16,DDD,0,0.04,False,0.0\n"
              "2026-11-17,CCC,-1,0.02,False,-0.0204\n2026-11-17,DDD,1,0.5,True,0.4996\n")   # void row dropped


@pytest.fixture()
def root(tmp_path):
    for sub, body in (("megacap_forward", SIX), ("megacap_ext", EXT)):
        (tmp_path / sub).mkdir()
        (tmp_path / sub / "marks.csv").write_text(body)
    return tmp_path


def test_ledger_takes_long_calls_of_both_sets_on_common_sessions(root):
    r = lo.ledger(root)
    d1 = (0.0196 + 0.0096) / 4                     # AAA and CCC long; BBB short and DDD flat count as 0
    d2 = -0.0204 / 3                               # BBB long; AAA flat, CCC short -> 0; the void DDD row is dropped
    assert (r["sessions"], r["since"], r["last"], r["calls"], r["wins"]) == (2, "2026-11-16", "2026-11-17", 3, 2)
    assert r["total"] == pytest.approx((1 + d1) * (1 + d2) - 1)
    assert r["long_short"] == pytest.approx((1 + (0.0196 + 0.0296 + 0.0096) / 4) * (1 + (-0.0204 - 0.0204) / 3) - 1)
    assert r["holding"] == pytest.approx((1 + (0.02 - 0.03 + 0.01 + 0.04) / 4) * (1 + (0.01 - 0.02 + 0.02) / 3) - 1)
    assert r["avg_call"] == pytest.approx((0.0196 + 0.0096 - 0.0204) / 3) and r["stop"] is False


def test_nothing_before_the_start_and_stop_rule(root):
    assert lo.ledger(root, start="2026-11-19") == {"sessions": 0, "calls": 0, "wins": 0}
    (root / "megacap_ext" / "marks.csv").write_text(HEAD + "2026-11-16,CCC,1,-0.9,False,-0.9004\n")
    assert lo.ledger(root)["stop"] is True         # (0.0196 - 0.9004) / 3 < -5%


@pytest.mark.parametrize("break_it", ["missing", "bad column", "bad number"])
def test_a_missing_or_unreadable_set_gives_an_empty_ledger(root, break_it):
    p = root / "megacap_ext" / "marks.csv"
    if break_it == "missing":
        p.unlink()
    elif break_it == "bad column":
        p.write_text("date,symbol\n2026-11-16,CCC\n")
    else:
        p.write_text(HEAD + "2026-11-16,CCC,1,abc,False,0.1\n")
    assert lo.ledger(root) == {"sessions": 0}
