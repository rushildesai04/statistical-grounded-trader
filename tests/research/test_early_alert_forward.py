"""Plumbing of prereg_early_alert_fade_forward_2026-11-18 on synthetic events (the decision is forward)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import early_alert_forward as st  # noqa: E402

D = "2026-11-10"


def ev(symbol, hhmmss, net, alert_type="volume_spike", session=D):
    return {"symbol": symbol, "session": session, "alert_type": alert_type, "posted_at": f"{session}T{hhmmss}+00:00", "net": net}


def lab(rows):
    return pd.DataFrame([{"symbol": s, "key": pd.Timestamp(f"{d}T{t}+00:00"), "news_own": n, "scen_band": b} for s, d, t, n, b in rows],
                        columns=st.FILL_COLS)


NO_FILLS = pd.DataFrame(columns=st.FILL_COLS)


def test_the_live_stamp_of_the_same_alert_comes_first_and_only_no_news_alerts_with_a_band_are_kept():
    scored = pd.DataFrame([ev("AAA", "15:00:01.400", -0.02), ev("NEWS", "15:01:00", 0.03), ev("UNK", "15:02:00", 0.03),
                           ev("NOBAND", "15:03:00", 0.03), ev("OLD", "15:00:00", 0.03, session="2026-11-03"), ev("NORES", "15:04:00", None),
                           ev("TYPE", "15:05:00", 0.03, "news"), ev("NA", "15:06:00", 0.01), ev("LATER", "15:07:00", 0.04)])
    labels = lab([("AAA", D, "15:00:01", 0, "early"), ("AAA", D, "15:30:00", 1, "late"),               # a later alert of AAA
                  ("NEWS", D, "15:01:00", 1, "middle"), ("UNK", D, "15:02:00", None, "middle"), ("NOBAND", D, "15:03:00", 0, None),
                  ("OLD", "2026-11-03", "15:00:00", 0, "early"), ("NORES", D, "15:04:00", 0, "early"), ("TYPE", D, "15:05:00", 0, "early"),
                  ("NA", D, "15:06:00", 0, "middle"), ("LATER", D, "15:07:30", 0, "late")])               # another alert, not this one
    s, fills, todo = st.resolve(scored, labels, NO_FILLS)                              # count-only: nothing is asked or filled
    assert [(r.symbol, r.scen_band) for r in s.itertuples()] == [("AAA", "early"), ("NA", "middle")]
    assert list(s.columns) == st.COLS and fills.empty and todo == {"news_unknown": 2, "band_unknown": 2, "filled_now": 0}
    assert st.resolve(scored.iloc[:0], labels, NO_FILLS)[0].empty


def test_what_the_live_system_never_stamped_is_filled_once_and_kept():
    scored = pd.DataFrame([ev("UNK", "15:02:00.300", 0.03), ev("NOROW", "15:08:00", 0.05), ev("NEWSY", "15:09:00", 0.02),
                           ev("DOWN", "15:10:00", 0.02), ev("TYPE", "15:11:00", 0.02, "news")])
    labels = lab([("UNK", D, "15:02:00", None, "late"), ("NEWSY", D, "15:09:00", None, "early"), ("DOWN", D, "15:10:00", None, "early")])
    asked = []

    def news_fn(sym, key):
        asked.append((sym, key.isoformat()))
        return {"UNK": 0, "NOROW": 0, "NEWSY": 1, "DOWN": None}[sym]
    bands = []

    def band_fn(sym, session, key):
        bands.append(sym)
        return "middle" if sym == "NOROW" else st.NO_BAND
    s, fills, todo = st.resolve(scored, labels, NO_FILLS, news_fn, band_fn)
    assert [(r.symbol, r.scen_band) for r in s.itertuples()] == [("UNK", "late"), ("NOROW", "middle")]
    assert ("UNK", "2026-11-10T15:02:00+00:00") in asked and "TYPE" not in {a for a, _ in asked}     # asked at the posting second
    assert bands == ["NOROW", "TYPE"] and todo == {"news_unknown": 1, "band_unknown": 0, "filled_now": 5}
    got = {r.symbol: (r.news_own, r.scen_band) for r in fills.itertuples()}
    assert got["NOROW"] == (0, "middle") and got["NEWSY"][0] == 1 and got["TYPE"][1] == st.NO_BAND and "DOWN" not in got
    asked.clear(), bands.clear()
    s2, fills2, todo2 = st.resolve(scored, labels, fills, news_fn, band_fn)             # the next night
    assert [a for a, _ in asked] == ["DOWN"] and bands == [] and s2.equals(s) and todo2["filled_now"] == 0
    s3, _, _ = st.resolve(scored, labels, fills)                                         # the count needs no network
    assert s3.equals(s)


def test_fills_survive_the_file_and_the_nightly_budget_is_bounded(tmp_path):
    scored = pd.DataFrame([ev(f"S{chr(65 + i)}", f"15:{i:02d}:00", 0.01) for i in range(6)] + [ev("NA", "15:30:00", 0.01)])
    n = []
    s, fills, todo = st.resolve(scored, lab([]), NO_FILLS, lambda sym, key: n.append(sym) or 0, lambda *a: "late", max_new=4)
    assert len(n) == 4 and len(s) == 4 and todo["news_unknown"] == 3
    fills.assign(key=fills.key.map(lambda t: pd.Timestamp(t).isoformat())).to_csv(tmp_path / "fills.csv", index=False)
    back = st.load_fills(tmp_path)
    assert "NA" in set(back.symbol) and str(back.key.dt.tz) == "UTC" and st.load_fills(tmp_path / "nothing").empty
    s2, _, todo2 = st.resolve(scored, lab([]), back)
    assert len(s2) == 4 and set(s2.scen_band) == {"late"} and todo2 == {"news_unknown": 3, "band_unknown": 0, "filled_now": 0}


def test_labels_are_read_from_the_store_read_only(tmp_path):
    p = tmp_path / "ops.db"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE fade_trades(id INTEGER PRIMARY KEY, symbol TEXT, session TEXT, posted_at TEXT, news_own INTEGER, scen_band TEXT)")
        c.executemany("INSERT INTO fade_trades(symbol, session, posted_at, news_own, scen_band) VALUES (?,?,?,?,?)",
                      [("NA", D, f"{D}T15:00:00.123456+00:00", None, "early"), ("NA", D, f"{D}T15:00:00.900000+00:00", 1, "early"),
                       ("OLD", "2026-11-03", "2026-11-03T15:00:00+00:00", 0, "late"), ("BAD", D, "not a time", 0, "late")])
    got = st.labels(p)
    assert list(got.symbol) == ["NA"] and got.scen_band.iloc[0] == "early" and got.news_own.iloc[0] == 1    # any flag in that second
    assert str(got.key.dt.tz) == "UTC"


def day_bars(low, at):
    """Pre-market bars from ``low`` up, then regular bars from 14:30 UTC with the alert-minute open at ``at``."""
    pre = pd.date_range(f"{D}T13:00:00Z", periods=30, freq="1min")
    rth = pd.date_range(f"{D}T14:30:00Z", periods=40, freq="1min")
    px = np.r_[np.linspace(low, at, 30), np.full(40, at)]
    return pd.DataFrame({"ts": list(pre) + list(rth), "segment": ["pre"] * 30 + ["rth"] * 40, "open": px, "high": px * 1.001,
                         "low": px * 0.999, "close": px, "volume": 1000.0})


def test_the_band_filled_here_is_the_nightly_label():
    posted = pd.Timestamp(f"{D}T14:40:20Z")
    assert st.band_of(day_bars(1.0, 1.2), posted) == "early" and st.band_of(day_bars(1.0, 1.5), posted) == "middle"
    assert st.band_of(day_bars(1.0, 2.0), posted) == "late"
    assert st.band_of(None, posted) == st.NO_BAND and st.band_of(day_bars(1.0, 1.2), pd.Timestamp(f"{D}T20:30:00Z")) == st.NO_BAND


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        if isinstance(self._b, Exception):
            raise self._b
        return self._b


class _Client:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def get(self, url, params=None):
        self.calls.append((url, params))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_the_news_flag_is_the_live_rules_lookup_at_the_alerts_time(monkeypatch, capsys):
    monkeypatch.setattr(st.time, "sleep", lambda s: None)
    at = pd.Timestamp(f"{D}T15:00:00Z")
    art = lambda t, syms, h="Company wins a contract": {"created_at": t, "symbols": syms, "headline": h}  # noqa: E731
    c = _Client(_Resp(200, {"news": [art(f"{D}T13:00:00Z", ["AAA"])]}))
    assert st.news_flag(c, "AAA", at) == 1
    url, p = c.calls[0]
    assert url == "/v1beta1/news" and p["symbols"] == "AAA" and p["end"] == at.to_pydatetime().isoformat() and p["limit"] == 50
    assert p["start"].startswith("2026-11-09T16:00")                                    # 16:00 ET of the weekday before
    assert st.news_flag(_Client(_Resp(200, {"news": []})), "AAA", at) == 0
    assert st.news_flag(_Client(_Resp(200, {"news": [art(f"{D}T13:00:00Z", ["AAA", "B", "C"])]})), "AAA", at) == 0   # a roundup
    assert st.news_flag(_Client(_Resp(200, {"news": [art(f"{D}T15:00:01Z", ["AAA"])]})), "AAA", at) == 0            # after the alert
    for bad in (_Resp(500, {}), _Resp(200, {"news": "x"}), _Resp(200, ValueError("no json")), TimeoutError("slow")):
        assert st.news_flag(_Client(bad), "AAA", at) is None
    quiet = _Client(_Resp(200, {"news": []}))
    assert st.news_flag(quiet, "bad symbol&x=1", at) is None and not quiet.calls
    assert capsys.readouterr().out.count("not read") == 4


def frame(early_mean, rest_mean, n=400, seed=0):
    rng = np.random.default_rng(seed)
    band = rng.choice(["early", "middle", "late"], n)
    net = np.where(band == "early", rng.normal(early_mean, 0.05, n), rng.normal(rest_mean, 0.05, n))
    return pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "session": [f"2026-12-{d:02d}" for d in rng.integers(1, 29, n)],
                         "alert_type": "volume_spike", "net": net, "scen_band": band})


def test_count_reads_no_result():
    c = st.count(frame(-0.02, 0.02, n=30), {"news_unknown": 2})
    assert set(c) == {"alerts_with_result_and_band", "sessions", "by_band", "needed", "news_unknown"}
    assert c["alerts_with_result_and_band"] == 30 and sum(c["by_band"].values()) == 30 and c["needed"] == 300


def test_pass_needs_the_gap_and_a_losing_early_third():
    good = st.decide(frame(-0.02, 0.02))
    assert good["pass"] and good["lower"] > 0 and good["mean_early"] < 0 and good["early"] + good["not_early"] == 400
    assert not st.decide(frame(0.01, 0.05))["pass"]                                # a gap, but the early alerts still earn
    assert not st.decide(frame(-0.02, -0.02))["pass"]                              # early alerts lose, but no gap
    one = st.decide(frame(-0.02, 0.02).assign(scen_band="late"))
    assert one["lower"] is None and not one["pass"]                                # nothing to compare: no bound = fail


def test_the_decision_waits_for_300_and_is_made_once(tmp_path):
    early = st.night(frame(-0.02, 0.02, n=299), None, tmp_path)
    assert "pass" not in early and not (tmp_path / "result.json").exists()
    res = st.night(frame(-0.02, 0.02, n=300), None, tmp_path)
    assert res["pass"] in (True, False) and json.loads((tmp_path / "result.json").read_text())["count"]["alerts_with_result_and_band"] == 300
    assert len(pd.read_csv(tmp_path / "sample.csv")) == 300
    again = st.night(frame(0.05, -0.05, n=400), None, tmp_path)                    # later data cannot change it
    assert "pass" not in again and json.loads((tmp_path / "result.json").read_text())["pass"] == res["pass"]


def test_the_nightly_step_is_wired_after_the_forward_record():
    sh = (Path(__file__).resolve().parents[2] / "deploy/fly/entrypoint.sh").read_text()
    a, b = sh.index("alert_fade_forward.py --no-telegram"), sh.index("early_alert_forward.py night")
    assert a < b and "run reports/early_alert_forward/run.log python /app/scripts/early_alert_forward.py night" in sh
    assert "reports/early_alert_forward" in sh.split("run() {")[0]
