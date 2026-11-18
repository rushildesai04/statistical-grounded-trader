"""Plumbing of prereg_long_feed_replay_2026-11-18 on synthetic bars (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_feed_replay_study as st  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402

POSTED = pd.Timestamp("2026-01-05 15:00:20", tz="UTC")           # 10:00:20 ET -> start bar 15:01
T1 = pd.Timestamp("2026-01-05 15:01", tz="UTC")


def bars(rows, start=T1):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for i, (o, h, lo, c, v) in enumerate(rows)])


# real tape: green, RED 15:02 (low 7.13), then 15:03 dips to 7.16 and runs through the 2x-risk target
TAPE = bars([(7.20, 7.50, 7.02, 7.50, 9000), (7.41, 7.54, 7.13, 7.21, 8000), (7.23, 7.50, 7.16, 7.44, 7000)]
            + [(7.45, 7.50, 7.40, 7.45, 500)] * 40)
# free feed: the same minutes on a sliver of the volume; its red candle only saw a low of 7.16
FEED = bars([(7.16, 7.40, 7.09, 7.40, 30), (7.42, 7.49, 7.16, 7.16, 20), (7.245, 7.245, 7.245, 7.245, 2)]
            + [(7.45, 7.46, 7.44, 7.45, 5)] * 40)


def event(**kw):
    d = dict(symbol="CLRO", session="2026-01-05", t0=T1, halt=False, cost_rt=0.008, posted_at=POSTED)
    d.update(kw)
    return SimpleNamespace(**d)


def test_a_stop_from_the_free_feeds_candle_is_hit_where_the_real_candles_stop_is_not():
    r = st.one_event(event(), TAPE, FEED)
    assert r["tape_kind"] == "L4a" and r["tape_stop"] == 7.13 and r["live_kind"] == "L4a" and r["live_stop"] == 7.16
    assert r["tape_net"] == pytest.approx((7.23 + 2 * 0.10) / 7.23 - 1 - 0.008)          # target on the fill bar
    assert r["live_net"] == pytest.approx(7.16 / 7.23 - 1 - 0.008) and r["live_fill_bar_stop"]
    assert r["pad0.005_net"] == pytest.approx((7.23 + 2 * (7.23 - 7.16 * 0.995)) / 7.23 - 1 - 0.008)   # the pad repairs it
    assert r["feed_volume_share"] == pytest.approx(FEED.volume.sum() / TAPE.volume.sum())


def test_contribution_is_size_times_net_with_the_books_risk_size():
    r = st.one_event(event(), TAPE, FEED)
    d = (7.23 - 7.13) / 7.23
    assert r["tape_d"] == pytest.approx(d) and r["tape_c"] == pytest.approx(min(1, st.D50 / d) * r["tape_net"])


def test_no_free_feed_bars_means_no_live_trade_but_the_tape_leg_stands():
    r = st.one_event(event(), TAPE, FEED.iloc[:0])
    assert "live_kind" not in r and r["tape_kind"] == "L4a" and r["feed_bars"] == 0 and r["feed_volume_share"] == 0.0


def test_a_live_order_that_does_not_fill_on_the_tape_is_void():
    late = pd.concat([TAPE.iloc[:2], bars([(7.00, 7.05, 6.90, 7.00, 100)] * 40, start=T1 + 2 * pt.BAR)], ignore_index=True)
    r = st.one_event(event(), late, FEED)                        # the tape opens below the live stop: no fill
    assert r["live_kind"] == "L4a" and r.get("live_void") is True and "live_net" not in r


def test_an_order_that_cannot_beat_its_cost_is_passed_over_on_both_feeds():
    r = st.one_event(event(cost_rt=0.50), TAPE, FEED)
    assert "tape_kind" not in r and "live_kind" not in r and r["base_kind"] == "L4a"     # the stored test had no cost check


def test_pick_takes_the_first_fill_with_the_books_tie_order():
    both = TAPE.copy()
    both.loc[2, "high"] = 7.63                                   # the fill bar also breaks the red candle's high
    found = pt.first_red(both, T1)
    cut = T1 + pd.Timedelta(hours=3)
    s, t = st.pick(found, both, cut, None)
    assert s.kind == "L4b" and t.entry_ts == T1 + 2 * pt.BAR     # L4b and L4a fill on the same bar: L4b first
    assert st.pick(pt.first_red(TAPE, T1), TAPE, cut, None)[0].kind == "L4a"
    assert st.pick(found, both, cut, 9.0) is None                # nothing can beat a cost of 900%


def test_parse_bars_keeps_only_whole_numeric_bars():
    good = {"t": "2026-01-05T15:01:00Z", "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 10}
    f = st.parse_bars([dict(good, t="2026-01-05T15:02:00Z"), good, {"t": "x", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1},
                       dict(good, o="1"), dict(good, h=True), dict(good, l=0), "junk", None])
    assert len(f) == 2 and f.ts.is_monotonic_increasing and list(f.columns) == st.COLS
    assert st.parse_bars(None).empty and st.parse_bars({"bars": 1}).empty and list(st.parse_bars([]).columns) == st.COLS
    assert len(st.parse_bars([good] * (st.MAX_ROWS + 5))) <= st.MAX_ROWS


def table(n=400, handicap=-0.004, pad_gain=0.003, seed=4):
    rng = np.random.default_rng(seed)
    sess = [f"2026-01-{d:02d}" for d in rng.integers(1, 29, n)]
    tape = rng.normal(-0.01, 0.02, n)
    live = tape + handicap + rng.normal(0, 0.002, n)
    t = pd.DataFrame({"symbol": "AAA", "session": sess, "halt": False, "feed_bars": 100, "feed_volume_share": rng.uniform(0.001, 0.05, n),
                      "tape_net": tape, "tape_c": tape, "live_net": live, "live_c": live, "tape_kind": "L4a", "live_kind": "L4a",
                      "tape_stop": 7.13, "live_stop": 7.16, "tape_entry_ts": T1, "live_entry_ts": T1})
    for p in st.PADS:
        t[f"pad{p:g}_net"] = t[f"pad{p:g}_c"] = live + pad_gain
        t[f"tape_pad{p:g}_net"] = t[f"tape_pad{p:g}_c"] = tape
    return t


def test_the_handicap_and_the_pad_gate():
    res = st.evaluate(table())
    assert res["A_handicap"]["real"] and res["A_handicap"]["mean"] == pytest.approx(-0.004, abs=5e-4)
    assert res["B_pass"] == ["0.5%", "1.0%", "2.0%"] and res["live_vs_tape"]["stop_tighter_share"] == 1.0
    flat = st.evaluate(table(pad_gain=0.0))
    assert flat["B_pass"] == [] and not flat["B_pads"]["1.0%"]["pass"]
    none = st.evaluate(table(handicap=0.0))
    assert not none["A_handicap"]["real"]


def test_a_leg_that_does_not_trade_counts_zero_in_the_paired_mean():
    t = table(n=200, handicap=0.0, pad_gain=0.0)
    t.loc[t.index[:100], ["live_c", "live_net"]] = np.nan
    d = st.paired(t, "live_c", "tape_c")
    assert d["mean"] == pytest.approx(float((t.live_c.fillna(0) - t.tape_c).mean()))


def test_reproduction_guard():
    t = pd.DataFrame({"symbol": ["A", "B"], "session": ["2026-01-05"] * 2, "halt": [False, False], "base_kind": ["L4a", "L6"],
                      "base_entry_ts": [T1, T1], "base_e_net": [0.01, -0.02]})
    base = pd.DataFrame({"symbol": ["A", "B"], "session": ["2026-01-05"] * 2, "halt": [False, False], "kind": ["L4a", "L6"],
                         "entry_ts": [str(T1), str(T1)], "e_net": [0.01, -0.02], "split": "train"})
    assert st.reproduces(t, base)[0]
    assert not st.reproduces(t.assign(base_e_net=[0.01, -0.03]), base)[0]
    assert not st.reproduces(t.assign(base_kind=["L4b", "L6"]), base)[0]
    assert not st.reproduces(t.iloc[:1], base)[0]
    extra = pd.concat([t, t.assign(symbol=["C", "D"])])
    assert not st.reproduces(extra, base)[0]


def test_a_second_run_is_refused(tmp_path, capsys):
    (tmp_path / "result_train.json").write_text("{}")
    assert st.main(["--out", str(tmp_path)]) == 2
    assert "not repeated" in capsys.readouterr().out


def test_the_held_out_events_stay_closed_without_a_training_pass(tmp_path, capsys):
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2 and "only after a training PASS" in capsys.readouterr().out
    (tmp_path / "result_train.json").write_text('{"B_pass": []}')
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2
    (tmp_path / "result_holdout.json").write_text("{}")
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2 and "not repeated" in capsys.readouterr().out


def test_per_trade_view_ignores_size():
    t = table(n=300, handicap=-0.004, pad_gain=0.002)
    v = st.same_trades(t)["1.0%"]
    assert v["trades"] == 300 and v["diff"] == pytest.approx(0.002) and v["diff_lo95"] <= v["diff"] <= v["diff_hi95"]
