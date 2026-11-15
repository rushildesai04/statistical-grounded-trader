"""Money score (prereg_money_score_2026-11-14): causal calibration, expected value, size multiplier, books."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.megacap import score


def rows(n_months=6, per=40, slope=0.5, seed=0, symbol="AAA"):
    rng = np.random.default_rng(seed)
    out = []
    for k in range(n_months):
        for d in range(per):
            pred = rng.normal(0, 0.01)
            out.append({"session": f"2020-{k + 1:02d}-{d % 28 + 1:02d}", "symbol": symbol, "pred": pred,
                        "label": slope * pred + rng.normal(0, 0.002), "d_rv21": 0.02,
                        "position": 1 if pred > 0.005 else (-1 if pred < -0.005 else 0)})
    return pd.DataFrame(out)


def test_calibration_uses_only_earlier_months_and_is_clipped():
    t = rows()
    b = score.calibration(t)
    assert b["2020-01"] == 0.0                                    # nothing earlier: no information, no trade
    assert b["2020-06"] == pytest.approx(0.5, abs=0.05)
    later = t.copy()
    later.loc[later.session >= "2020-04-01", "label"] = -9.0     # the future (April on) changes...
    b2 = score.calibration(later)
    assert [b2[m] for m in ("2020-02", "2020-03", "2020-04")] == [b[m] for m in ("2020-02", "2020-03", "2020-04")]
    assert b2["2020-06"] == 0.0                                   # ...only later months; a negative slope clips to 0
    assert score.calibration(rows(slope=3.0))["2020-06"] == 1.0   # and a slope above 1 clips to 1


def test_expected_value_is_signed_by_side_and_net_of_cost():
    t = pd.DataFrame({"session": ["2020-02-03"] * 4, "symbol": ["AAA", "AAA", "RDDT", "AAA"],
                      "pred": [0.01, -0.01, 0.01, 0.01], "label": 0.0, "d_rv21": [0.02, 0.02, 0.05, 0.0],
                      "position": [1, -1, 1, 0]})
    e = score.expected(t, {"2020-02": 0.5})
    assert e.ev.iloc[0] == pytest.approx(0.005 - 0.0004) and e.ev.iloc[1] == pytest.approx(0.005 - 0.0004)
    assert e.ev.iloc[2] == pytest.approx(0.005 - 0.0010) and np.isnan(e.ev.iloc[3])
    assert e.z.iloc[0] == pytest.approx(0.0046 / 0.02) and e.z.iloc[2] == pytest.approx(0.004 / 0.05)
    assert (score.expected(t, {}).ev.dropna() < 0).all()         # no calibration for the month: minus the cost


def test_multiplier_is_zero_without_positive_expected_value_and_clipped_otherwise():
    t = pd.DataFrame({"month": ["2020-03"] * 6, "position": [1, 1, 1, 1, 0, -1],
                      "ev": [0.004, 0.0004, 0.04, -0.001, np.nan, 0.004],
                      "z": [0.2, 0.02, 2.0, -0.05, np.nan, 0.2]})
    m = score.multiplier(t, {"2020-03": 0.2})
    assert m.tolist() == pytest.approx([1.0, 0.25, 2.0, 0.0, 0.0, 1.0])
    assert score.multiplier(t, {"2020-03": float("nan")}).tolist() == pytest.approx([1.0, 1.0, 1.0, 0.0, 0.0, 1.0])


def test_reference_is_the_median_of_earlier_positive_calls():
    t = pd.DataFrame({"month": ["2020-01", "2020-01", "2020-01", "2020-02", "2020-03"], "position": [1, -1, 1, 1, 1],
                      "ev": [0.01, 0.03, -0.01, 0.05, 0.01], "z": [0.1, 0.3, -0.1, 0.5, 0.1]})
    ref = score.reference(t)
    assert np.isnan(ref["2020-01"]) and ref["2020-02"] == pytest.approx(0.2) and ref["2020-03"] == pytest.approx(0.3)


def test_book_caps_each_name_scales_a_full_day_and_charges_cost():
    t = pd.DataFrame({"session": ["d1"] * 5 + ["d2"], "symbol": list("ABCDE") + ["A"], "position": [1, 1, 1, 1, -1, 0],
                      "label": [0.01, 0.01, 0.01, 0.01, -0.01, 0.5], "d_rv21": [0.02] * 6})
    w = score.base_weight(t.d_rv21)
    assert w.iloc[0] == pytest.approx(0.25)                       # 1% / 2% = 50% -> capped at 25%
    b = score.book(t, w)
    assert b.gross["d1"] == pytest.approx(1.0)                    # 5 x 25% scaled down to 100%
    assert b.ret["d1"] == pytest.approx(5 * 0.2 * (0.01 - 0.0004))
    assert b.gross["d2"] == 0.0 and b.ret["d2"] == 0.0            # flat row: no weight, no return
    assert score.base_weight(pd.Series([0.0, np.nan, 0.10])).tolist() == pytest.approx([0.0, 0.0, 0.10])


def _calls(rows):
    return pd.DataFrame(rows, columns=["session", "symbol", "position", "net"])


def test_record_ev_shrinks_a_stocks_earlier_calls_toward_the_sides_pooled_mean():
    c = _calls([("2020-01-10", "A", 1, 0.02), ("2020-01-11", "A", 1, 0.04), ("2020-01-12", "B", 1, -0.03),
                ("2020-01-13", "A", -1, 0.50),                       # the other side never mixes in
                ("2020-02-03", "A", 1, 9.0), ("2020-02-04", "B", 1, 9.0), ("2020-02-05", "C", 1, 9.0),
                ("2020-02-06", "A", -1, 9.0), ("2020-03-02", "A", 1, 0.0)])
    ev = score.record_ev(c, k=2.0)
    assert ev.iloc[:4].tolist() == [0.0, 0.0, 0.0, 0.0]              # nothing earlier in the first month
    pooled = (0.02 + 0.04 - 0.03) / 3
    assert ev.iloc[4] == pytest.approx((0.06 + 2 * pooled) / (2 + 2))   # A long: 2 earlier calls
    assert ev.iloc[5] == pytest.approx((-0.03 + 2 * pooled) / (1 + 2))  # B long: 1 earlier call
    assert ev.iloc[6] == pytest.approx(pooled)                          # C long: no record of its own -> pooled
    assert ev.iloc[7] == pytest.approx((0.50 + 2 * 0.50) / (1 + 2))     # A short: only shorts
    pooled3 = (0.02 + 0.04 - 0.03 + 27.0) / 6
    assert ev.iloc[8] == pytest.approx((0.06 + 9.0 + 2 * pooled3) / (3 + 2))   # March sees January and February
    assert list(ev.index) == list(c.index)


def test_record_ev_never_uses_the_calls_own_month_or_later():
    c = _calls([("2020-01-10", "A", 1, 0.01), ("2020-02-03", "A", 1, 0.02), ("2020-02-20", "A", 1, 0.03),
                ("2020-03-02", "A", 1, 0.04)])
    base = score.record_ev(c)
    later = c.copy()
    later.loc[later.session >= "2020-02-01", "net"] = -5.0
    got = score.record_ev(later)
    assert got.iloc[:3].tolist() == pytest.approx(base.iloc[:3].tolist())   # February's value: January only
    assert got.iloc[3] < 0 < base.iloc[3]                                    # March sees the changed February
