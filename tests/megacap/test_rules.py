"""Rule lines of prereg_long_rules_2026-11-15: which calls an earnings release skips, and what the crowded-day cap may
know at the 15:50 decision. Synthetic data only: the real record is read once, by the study."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.megacap import rules, score

SESSIONS = ["2026-03-05", "2026-03-06", "2026-03-09", "2026-03-10"]        # Thu, Fri, Mon, Tue


def at(*stamps: str) -> pd.Series:
    return pd.Series(pd.to_datetime(list(stamps)).tz_localize("America/New_York"))


@pytest.mark.parametrize("stamp, hold", [
    ("2026-03-05 16:00:00", {"2026-03-05"}),      # at the close of D: inside D's hold
    ("2026-03-05 15:59:59", set()),               # before the first close: the hold of an unknown earlier session
    ("2026-03-06 15:59:59", {"2026-03-05"}),      # just before the next close: still D's hold
    ("2026-03-06 16:00:00", {"2026-03-06"}),      # at the next close: the next session's hold
    ("2026-03-06 07:00:00", {"2026-03-05"}),      # before the open
    ("2026-03-07 12:00:00", {"2026-03-06"}),      # a weekend release lands in Friday's hold
    ("2026-03-09 09:00:00", {"2026-03-06"}),
    ("2026-03-10 16:05:00", set()),               # after the last close: no known next session
])
def test_hold_window_boundaries(stamp, hold):
    assert rules.hold_sessions(at(stamp), SESSIONS) == hold


def test_hold_sessions_reads_utc_stamps_in_new_york_time():
    utc = pd.Series(pd.to_datetime(["2026-03-05 21:05:00"]).tz_localize("UTC"))     # 16:05 ET
    assert rules.hold_sessions(utc, SESSIONS) == {"2026-03-05"}
    assert rules.hold_sessions(at(), SESSIONS) == set() and rules.hold_sessions(at("2026-03-05 17:00"), ["2026-03-05"]) == set()


def table(n: int = 80, symbols=("AAA", "BBB", "CCC"), seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-02", periods=n)]
    rows = [{"session": d, "symbol": s, "label": float(rng.normal(0, 0.02)), "pred": 0.0, "position": 1, "d_rv21": 0.02}
            for d in days for s in symbols]
    return pd.DataFrame(rows)


def test_only_the_reporting_stock_is_flagged():
    t = table(6)
    days = sorted(t.session.unique())
    flag = rules.earnings_flag(t, {"BBB": at(f"{days[2]} 16:20:00"), "ZZZ": at(f"{days[1]} 16:20:00")})
    assert t[flag][["session", "symbol"]].values.tolist() == [[days[2], "BBB"]]
    assert not rules.earnings_flag(t, {}).any()


def test_a_skipped_call_frees_no_weight_for_the_others():
    t = table(3)
    w0 = rules.scaled(t, pd.Series(0.5, index=t.index))                    # 3 x 25% cap = 75%: under the gross cap
    assert w0.tolist() == [0.25] * len(t)
    t5 = table(3, symbols=("A", "B", "C", "D", "E"))
    w5 = rules.scaled(t5, pd.Series(0.5, index=t5.index))                  # 5 x 25% = 125% -> scaled to 100%
    assert w5.groupby(t5.session).sum().round(9).tolist() == [1.0] * 3 and w5.round(9).eq(0.2).all()
    cut = w5.where(t5.symbol != "A", 0.0)
    held = score.book(t5, cut)
    assert held.gross.round(9).tolist() == [0.8] * 3                       # the other four stay at 20% each
    flat = t5.assign(position=0)
    assert rules.scaled(flat, pd.Series(0.5, index=flat.index)).eq(0).all()


def test_swing_knows_crowding_and_nothing_after_two_sessions_back():
    t = table(80, symbols=("AAA", "BBB"))
    twin = t.copy()
    twin.loc[twin.symbol == "BBB", "label"] = twin.loc[twin.symbol == "AAA", "label"].to_numpy()   # BBB moves as AAA
    w = pd.Series(0.25, index=t.index)
    days = sorted(t.session.unique())
    d = days[-1]
    apart, together = rules.book_swing(t, w)[d], rules.book_swing(twin, w)[d]
    win = twin[(twin.symbol == "AAA") & twin.session.isin(days[-62:-2])].label
    assert together == pytest.approx(0.5 * win.std(ddof=1)) and together > apart * 1.2
    late = twin.copy()                                                    # the rows of D-1 and D are not known at 15:50
    late.loc[late.session.isin(days[-2:]), "label"] = 9.0
    assert rules.book_swing(late, w)[d] == together
    early = twin.copy()
    early.loc[early.session == days[-3], "label"] = 9.0                   # D-2 is known: it must move the number
    assert rules.book_swing(early, w)[d] != together


def test_swing_without_history_uses_the_stocks_own_risk_and_no_covariance():
    t = table(10, symbols=("AAA", "BBB"))
    w = pd.Series(0.25, index=t.index)
    s = rules.book_swing(t, w)
    assert s.iloc[-1] == pytest.approx(np.sqrt(2 * (0.25 * 0.02) ** 2)) and len(s) == 10
    assert rules.book_swing(t, w.where(t.symbol == "AAA", 0.0)).iloc[0] == pytest.approx(0.25 * 0.02)
    assert rules.book_swing(t, w * 0).empty


def test_cap_uses_earlier_sessions_only_and_never_raises():
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-02", periods=70)]
    s = pd.Series(0.01, index=days)
    s.iloc[65] = 0.04
    s.iloc[66] = 0.005
    f = rules.cap_factor(s)
    assert f.iloc[:60].eq(1.0).all()                                       # no cap until 60 earlier sessions
    assert f.iloc[65] == pytest.approx(0.25) and f.iloc[66] == 1.0 and f.max() == 1.0
    later = s.copy()
    later.iloc[67:] = 5.0                                                  # the future never moves an earlier cap
    assert rules.cap_factor(later).iloc[:67].equals(f.iloc[:67])
    own = s.copy()
    own.iloc[65] = 0.02                                                    # the day's own swing is not in its own cap
    assert rules.cap_factor(own).iloc[65] == pytest.approx(0.5)
    assert rules.cap_factor(pd.Series([0.0] * 70, index=days)).eq(1.0).all()
    mixed = pd.Series([0.01, 0.03] * 30 + [0.05], index=days[:61])             # 60 earlier: median 0.02; with itself 0.03
    assert rules.cap_factor(mixed).iloc[60] == pytest.approx(0.4)
