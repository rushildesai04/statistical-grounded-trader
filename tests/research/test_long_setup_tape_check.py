"""The full-tape setup check (prereg_long_setup_tape_2026-11-22): its classes, on bars built by hand."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_setup_tape_check as st  # noqa: E402

E = {"id": 1, "symbol": "ABCD", "session": "2026-11-02", "posted_at": "2026-11-02T14:30:40+00:00"}


def mk(closes):
    ts = pd.date_range(pd.Timestamp("2026-11-02 14:31", tz="UTC"), periods=len(closes), freq="1min")
    c = pd.Series(closes, dtype=float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c + 0.01, "low": c - 0.01, "close": c, "volume": 1000.0})


FLAT = mk([5.0] * 16)                                   # no base break: nothing closes above the range
BREAK = mk([5.0] * 12 + [5.2] * 4)                      # twelve quiet minutes, then a close above them and above VWAP
LATER = mk([5.0] * 13 + [5.2] * 3)                      # the same break one minute later


def test_the_live_rules_pick_is_the_base_break_at_its_candles_close():
    kind, at, stop = st.pick(BREAK, E)
    assert (kind, at, stop) == ("L8", pd.Timestamp("2026-11-02 14:44", tz="UTC"), 4.99)
    assert st.pick(FLAT, E) is None


def test_one_class_per_alert_for_the_live_rule():
    got = {name: st.agree(E, feed, tape)["class"] for name, feed, tape in (
        ("same", BREAK, BREAK), ("thin", BREAK, FLAT), ("tape", FLAT, BREAK), ("none", FLAT, FLAT),
        ("diff", BREAK, LATER), ("nodata", None, FLAT))}
    assert got == {"same": "SAME", "thin": "THIN FEED ONLY", "tape": "TAPE ONLY", "none": "BOTH NONE",
                   "diff": "DIFFERENT", "nodata": "NO DATA"}


def test_a_recorded_setup_is_classed_against_the_tape_only_after_the_feed_reproduces_it():
    r = {**E, "kind": "L8", "status": "settled", "net": -0.02, "stop": 4.99, "est_entry": 5.2,
         "entry_ts": "2026-11-02T14:44:00+00:00"}
    assert st.classify(r, BREAK, BREAK)["class"] == "EXISTED"
    assert st.classify(r, BREAK, LATER)["class"] == "EXISTED, TIMING DIFFERED"
    thin = st.classify(r, BREAK, FLAT)
    assert (thin["class"], thin["why"], thin["lost"]) == ("THIN FEED ONLY", "no such setup on the tape", True)
    assert st.classify(r, FLAT, BREAK)["class"] == "INSUFFICIENT EVIDENCE"      # the control fails: no class is guessed
    assert st.classify({**r, "stop": 4.90}, BREAK, BREAK)["class"] == "INSUFFICIENT EVIDENCE"   # another stop: not that setup
    assert st.classify(r, BREAK, None)["class"] == "INSUFFICIENT EVIDENCE"
