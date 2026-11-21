"""Plumbing of prereg_borrow_scan_forward_2026-11-18 on synthetic lists and bars (the decision is forward)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import borrow_scan as st  # noqa: E402


def snap(rows):
    return pd.DataFrame(rows, columns=["symbol", "avail", "fee"]).set_index("symbol")


def write(root: Path, day: str, rows):
    d = root / day.replace("-", "")
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["symbol", "avail", "fee"]).assign(rebate=0.0, snap="x").to_parquet(d / "2045.parquet")   # 15:45 ET


def test_dry_and_fee_events_follow_the_frozen_thresholds():
    prev = snap([("DRY1", 500_000, 5), ("NOTDRY", 500_000, 5), ("SMALL", 50_000, 5), ("FEE1", 1_000_000, 12), ("FEE2", 1_000_000, 8),
                 ("BOTH", 200_000, 15), ("GONE", 300_000, 5)])
    cur = snap([("DRY1", 9_000, 5), ("NOTDRY", 60_000, 5), ("SMALL", 0, 5), ("FEE1", 1_000_000, 24), ("FEE2", 1_000_000, 18),
                ("BOTH", 5_000, 40), ("NEW", 0, 300)])
    c = st.candidates(prev, cur)
    got = {(r.symbol, r.kind) for r in c.itertuples()}
    assert got == {("DRY1", "DRY"), ("BOTH", "DRY"), ("FEE1", "FEE"), ("BOTH", "FEE")}
    # NOTDRY kept 12% of its shares; SMALL had under 100,000; FEE2 doubled but is under 20%; GONE / NEW are in one list only
    r = c[(c.symbol == "DRY1")].iloc[0]
    assert (r.avail_prev, r.avail, r.fee) == (500_000, 9_000, 5)


def test_daily_bars_parse_strictly():
    good = {"t": "2026-11-17T05:00:00Z", "c": 5.0, "v": 1_000_000}
    j = {"bars": {"AAA": [good], "BBB": [dict(good, t="2026-11-16T05:00:00Z")], "CCC": [dict(good, c="5")], "DDD": [dict(good, c=0)],
                  "EEE": "junk", "FFF": [None, dict(good, v=True)]}}
    assert st.parse_daily(j, "2026-11-17") == {"AAA": (5.0, 1_000_000.0)}
    assert st.parse_daily(None, "2026-11-17") == {} and st.parse_daily({"bars": []}, "2026-11-17") == {}


def test_screen_needs_price_and_dollar_volume_and_respects_the_cooldown():
    order = st.sessions("2026-11-02", date(2026, 11, 30))
    cand = pd.DataFrame({"symbol": ["OK", "CHEAP", "THIN", "NOBAR", "AGAIN"], "kind": "DRY", "avail_prev": 2e5, "avail": 0.0,
                         "fee_prev": 5.0, "fee": 9.0})
    bars = {"OK": (10.0, 500_000), "CHEAP": (1.5, 9_000_000), "THIN": (10.0, 100_000), "AGAIN": (10.0, 500_000)}
    past = pd.DataFrame({"symbol": ["AGAIN"], "session": ["2026-11-10"], "kind": ["DRY"]})
    got = st.screen(cand, bars, "2026-11-17", past, order)                          # 5 sessions after AGAIN's event
    assert list(got.symbol) == ["OK"] and got.dollar_volume.iloc[0] == 5_000_000 and list(got.columns) == st.COLS
    later = st.screen(cand, bars, "2026-11-25", past, order)                        # 11 sessions after: counts again
    assert sorted(later.symbol) == ["AGAIN", "OK"]
    fee = st.screen(cand.assign(kind="FEE"), bars, "2026-11-17", past, order)       # the cooldown is per kind
    assert sorted(fee.symbol) == ["AGAIN", "OK"]


def test_record_processes_each_finished_session_once(tmp_path):
    root, out = tmp_path / "borrow", tmp_path / "out"
    write(root, "2026-11-04", [("AAA", 500_000, 5), ("BBB", 500_000, 5)])
    write(root, "2026-11-05", [("AAA", 1_000, 5), ("BBB", 500_000, 5)])
    write(root, "2026-11-06", [("AAA", 1_000, 5), ("BBB", 2_000, 5)])
    asked = []

    def bars(symbols, day):
        asked.append((tuple(symbols), day))
        return {s: (8.0, 1_000_000) for s in symbols}
    r = st.record(date(2026, 11, 7), bars, out, root)                               # the 6th is the last finished session
    ev = pd.read_csv(out / "events.csv")
    assert [(e.symbol, e.session, e.kind) for e in ev.itertuples()] == [("AAA", "2026-11-05", "DRY"), ("BBB", "2026-11-06", "DRY")]
    assert r["new_events"] == 2 and r["DRY_events"] == 2 and r["DRY_with_5_session_result"] == 0
    assert json.loads((out / "sessions.json").read_text()) == ["2026-11-05", "2026-11-06"]
    again = st.record(date(2026, 11, 7), bars, out, root)
    assert again["new_events"] == 0 and len(asked) == 2 and len(pd.read_csv(out / "events.csv")) == 2


def test_a_failed_bar_request_or_a_missing_list_leaves_the_day_for_another_night(tmp_path):
    root, out = tmp_path / "borrow", tmp_path / "out"
    write(root, "2026-11-04", [("AAA", 500_000, 5)])
    write(root, "2026-11-05", [("AAA", 1_000, 5)])
    write(root, "2026-11-09", [("AAA", 1_000, 5)])                                  # the 6th has no list
    r = st.record(date(2026, 11, 10), lambda s, d: None, out, root)
    assert r["new_events"] == 0 and json.loads((out / "sessions.json").read_text()) == []
    ok = st.record(date(2026, 11, 10), lambda s, d: {x: (8.0, 1_000_000) for x in s}, out, root)
    assert ok["new_events"] == 1 and json.loads((out / "sessions.json").read_text()) == ["2026-11-05"]


def test_a_failure_stops_the_run_so_later_sessions_never_overtake_an_earlier_one(tmp_path):
    root, out = tmp_path / "borrow", tmp_path / "out"
    write(root, "2026-11-04", [("AAA", 500_000, 5), ("BBB", 500_000, 5)])
    write(root, "2026-11-05", [("AAA", 1_000, 5), ("BBB", 500_000, 5)])
    write(root, "2026-11-06", [("AAA", 1_000, 5), ("BBB", 2_000, 5)])
    ok = lambda s, d: {x: (8.0, 1_000_000) for x in s}  # noqa: E731
    r = st.record(date(2026, 11, 7), lambda s, d: None if d == "2026-11-05" else ok(s, d), out, root)
    assert r["waiting"] == "2026-11-05" and r["new_events"] == 0 and json.loads((out / "sessions.json").read_text()) == []
    r = st.record(date(2026, 11, 7), ok, out, root)
    assert r["waiting"] is None and [e.session for e in pd.read_csv(out / "events.csv").itertuples()] == ["2026-11-05", "2026-11-06"]


def test_a_broken_list_gives_no_events_and_is_written_down(tmp_path, monkeypatch):
    root, out = tmp_path / "borrow", tmp_path / "out"
    names = [f"S{chr(65 + i)}" for i in range(6)]
    write(root, "2026-11-04", [(n, 500_000, 5) for n in names])
    write(root, "2026-11-05", [(n, 0, 5) for n in names])                           # every name "dries up" at once
    monkeypatch.setattr(st, "MAX_CANDIDATES", 5)
    asked = []
    r = st.record(date(2026, 11, 6), lambda s, d: asked.append(d) or {}, out, root)
    assert r["broken_lists"] == ["2026-11-05"] and r["new_events"] == 0 and not asked
    assert json.loads((out / "broken.json").read_text()) == ["2026-11-05"] and json.loads((out / "sessions.json").read_text()) == ["2026-11-05"]


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._b = code, body

    def json(self):
        if isinstance(self._b, Exception):
            raise self._b
        return self._b


class _Client:
    def __init__(self, answers):
        self.answers, self.calls = list(answers), []

    def get(self, url, headers=None, params=None):
        self.calls.append(params)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def test_only_plain_tickers_are_asked_and_any_bad_answer_fails_the_day(monkeypatch, capsys):
    monkeypatch.setattr(st.time, "sleep", lambda s: None)
    bar = {"t": "2026-11-17T05:00:00Z", "c": 5.0, "v": 1_000_000}
    c = _Client([_Resp(200, {"bars": {"AAA": [bar]}})])
    got = st.day_bars(c, "u", {}, ["AAA", "BRK B", "x&y=1", "BRK.B", "TOOLONGG", None, "aaa"], "2026-11-17")
    assert got == {"AAA": (5.0, 1_000_000.0)} and c.calls[0]["symbols"] == "AAA,BRK.B"
    assert (c.calls[0]["start"], c.calls[0]["end"]) == ("2026-11-17T00:00:00Z", "2026-11-17T23:59:59Z")    # never a future end
    for bad in (_Resp(429, {}), _Resp(200, ["x"]), _Resp(200, ValueError("no json")), TimeoutError("slow"),
                _Resp(200, {"bars": {}, "next_page_token": "more"})):
        assert st.day_bars(_Client([bad]), "u", {}, ["AAA"], "2026-11-17") is None
    assert capsys.readouterr().out.count("could not be read") == 5
    assert st.day_bars(_Client([]), "u", {}, ["bad symbol"], "2026-11-17") == {}          # nothing to ask: no request


def test_count_reads_no_outcome_and_knows_which_events_are_five_sessions_old():
    order = st.sessions("2026-11-04", date(2026, 11, 18))
    ev = pd.DataFrame({"symbol": ["A", "B", "C"], "session": ["2026-11-05", "2026-11-11", "2026-11-17"], "kind": ["DRY", "DRY", "FEE"]})
    c = st.count(ev, order)
    assert c == {"sessions": 11, "DRY_events": 2, "FEE_events": 1, "DRY_with_5_session_result": 2, "FEE_with_5_session_result": 0, "needed": 300}
    assert st.count(ev.iloc[:0], order)["DRY_events"] == 0


def test_outcome_is_hedged_and_the_short_pays_cost_and_fee():
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-11-02", periods=12)]
    stock, spy = pd.Series(np.linspace(10, 12.2, 12), index=idx), pd.Series(np.linspace(400, 404.4, 12), index=idx)
    ev = pd.DataFrame({"symbol": ["A"], "session": [idx[2]], "kind": ["FEE"], "fee": [72.0]})
    o = st.outcome(ev, lambda s: stock, spy)
    r5 = o[o.hold == 5].iloc[0]
    rel = (stock.iloc[7] / stock.iloc[2] - 1) - (spy.iloc[7] / spy.iloc[2] - 1)
    days = (pd.Timestamp(idx[7]) - pd.Timestamp(idx[2])).days
    assert r5.long == pytest.approx(rel - st.COST) and r5.short == pytest.approx(-rel - st.COST - 0.72 * days / 360)
    assert len(o) == 2 and st.outcome(ev, lambda s: None, spy).empty


def test_decide_needs_a_lower_bound_above_zero():
    rng = np.random.default_rng(0)
    n = 400
    o = pd.DataFrame({"session": [f"2026-12-{d:02d}" for d in rng.integers(1, 29, n)], "kind": "DRY", "hold": 5,
                      "long": rng.normal(0.02, 0.05, n), "short": rng.normal(-0.03, 0.05, n)})
    d = st.decide(o)
    assert d["DRY | 5 | long"]["pass"] and not d["DRY | 5 | short"]["pass"] and d["FEE | 1 | long"] == {"events": 0, "mean": None, "lower": None, "pass": False}


def test_the_job_is_scheduled_wired_and_explained():
    root = Path(__file__).resolve().parents[2]
    assert "job borrowscan" in (root / "deploy/fly/crontab").read_text()
    assert "borrowscan) run reports/borrow_scan/run.log python /app/scripts/borrow_scan.py record" in (root / "deploy/fly/entrypoint.sh").read_text()
    assert '"borrowscan"' in (root / "scripts/ops_notify.py").read_text()
