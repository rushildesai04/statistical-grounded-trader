"""Plumbing of prereg_gap_news_2026-11-18 on synthetic data (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import gap_news_study as st  # noqa: E402


@pytest.mark.parametrize("headline,kind", [
    ("Acme Announces Pricing of $20 Million Public Offering", "OFFERING"),
    ("Acme Reports Q3 Results and Announces Registered Direct Offering", "OFFERING"),       # the offering wins over earnings
    ("Acme Q2 EPS Beats Estimates, Raises Guidance", "EARNINGS"),
    ("Acme Announces Positive Topline Phase 2 Data", "CLINICAL"),
    ("Acme Receives FDA Clearance For Its Device", "CLINICAL"),
    ("Acme Signs $40M Supply Agreement With Globex", "DEAL"),
    ("Acme To Be Acquired By Globex", "DEAL"),
    ("HC Wainwright Maintains Buy On Acme, Raises Price Target To $9", "EARNINGS"),         # "raises": the frozen order decides
    ("Needham Initiates Coverage On Acme With Buy", "ANALYST"),
    ("Acme Shares Are Trading Higher Today", "OTHER"),
])
def test_kind_is_the_first_pattern_in_the_frozen_order(headline, kind):
    assert st.kind_of([headline]) == kind


def test_no_headline_is_none_and_any_matching_headline_decides():
    assert st.kind_of([]) == "NONE"
    assert st.kind_of(["Acme Shares Are Trading Higher", "Acme Prices Upsized Offering"]) == "OFFERING"
    assert st.KINDS == ("OFFERING", "EARNINGS", "CLINICAL", "DEAL", "ANALYST", "OTHER", "NONE")


def archive(tmp_path, rows):
    for month, part in pd.DataFrame(rows, columns=["created_at", "symbols", "headline"]).groupby(lambda i: rows[i][0][:7]):
        part.to_parquet(tmp_path / f"{month}.parquet")
    return st.Headlines(tmp_path)


def test_headlines_are_company_news_from_the_prior_close_to_the_open(tmp_path):
    h = archive(tmp_path, [
        ("2026-03-02T20:59:00Z", "ACME", "too early: before Monday's close"),             # 15:59 ET Monday
        ("2026-03-02T21:05:00Z", "ACME", "after Monday's close"),
        ("2026-03-03T13:00:00Z", "ACME,GLBX", "pre-market, two symbols"),
        ("2026-03-03T13:10:00Z", "ACME,GLBX,SPY", "roundup of three"),
        ("2026-03-03T14:30:00Z", "ACME", "at the open: too late"),                        # 09:30 ET
        ("2026-03-03T13:20:00Z", "OTHR", "another company"),
        ("bad", "ACME", "unreadable time"),
    ])
    assert h.before_open("ACME", "2026-03-03") == ["after Monday's close", "pre-market, two symbols"]
    assert h.before_open("ACME", "2026-03-04") == [] and h.before_open("NOPE", "2026-03-03") == []


def test_a_monday_gap_reads_the_weekend_and_a_month_boundary(tmp_path):
    h = archive(tmp_path, [("2026-02-28T15:00:00Z", "ACME", "Saturday news"), ("2026-02-27T21:30:00Z", "ACME", "Friday after the close"),
                           ("2026-02-27T20:00:00Z", "ACME", "Friday before the close")])
    assert sorted(h.before_open("ACME", "2026-03-02")) == ["Friday after the close", "Saturday news"]


def test_trade_is_from_the_first_open_to_one_hour_later_and_to_the_close():
    ts = pd.date_range("2026-03-03 14:30", periods=390, freq="1min", tz="UTC")
    rth = pd.DataFrame({"ts": ts, "open": 10.0, "close": np.linspace(10.0, 12.0, 390)})
    t = st.trade(rth)
    assert t["px"] == 10.0 and t["g_60"] == pytest.approx(rth.close[59] / 10 - 1) and t["g_close"] == pytest.approx(0.2)
    assert st.trade(rth.iloc[:0]) is None and st.trade(rth.assign(open=0.0)) is None


def table(n=3000, seed=2, offering_fade=0.0):
    rng = np.random.default_rng(seed)
    days = np.array([f"2025-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 26)])
    t = pd.DataFrame({"symbol": "AAA", "session": rng.choice(days, n), "gap": rng.uniform(10, 80, n), "liquid": rng.random(n) < 0.8,
                      "kind": rng.choice(st.KINDS, n), "px": rng.uniform(0.5, 12, n), "cost": 0.009,
                      "g_60": rng.normal(0, 0.06, n), "g_close": rng.normal(0, 0.09, n)})
    t.loc[t.kind == "OFFERING", "g_60"] -= offering_fade
    return t


def test_gate_is_per_kind_exit_and_side_after_costs_and_the_locate():
    r = st.evaluate(table())
    assert r["passes"] == [] and len(r["cells"]) == 14 and r["alpha_each"] == st.ALPHA / 28
    f = st.evaluate(table(offering_fade=0.06))
    assert f["passes"] == ["OFFERING | 60 | short"]
    c = f["cells"]["OFFERING | 60"]
    assert c["short_net"] == pytest.approx(-c["gross"] - 0.009 - st.LOCATE) and c["long_net"] == pytest.approx(c["gross"] - 0.009)
    assert set(c["by_gap"]) == {"gap 10-20%", "gap 20-50%", "gap above 50%"} and "long_pass" not in c["by_gap"]["gap 10-20%"]


def test_small_cells_fail_closed_and_thin_gaps_are_kept_apart():
    t = table(n=700, offering_fade=0.3)
    r = st.evaluate(t)
    assert r["passes"] == [] and r["cells"]["OFFERING | 60"]["short_lower"] is None        # under 150 gaps
    assert r["gaps"] == int(t.liquid.sum()) and "OFFERING | 60" in r["thin_apart"]


def test_held_out_scores_only_what_passed(tmp_path, capsys):
    r = st.evaluate(table(offering_fade=0.06, seed=9), only=["OFFERING | 60 | short"])
    assert list(r["cells"]) == ["OFFERING | 60"] and r["passes"] == ["OFFERING | 60 | short"] and r["alpha_each"] == st.ALPHA
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2 and "only for a cell that passed" in capsys.readouterr().out
    (tmp_path / "result_train.json").write_text('{"passes": []}')
    assert st.main(["--out", str(tmp_path)]) == 2 and "not repeated" in capsys.readouterr().out
