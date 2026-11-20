"""Forward record of the frozen intraday trend rule on SPY / QQQ (owner 2026-11-19: record only, no trades)."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import noise_area_forward as nf  # noqa: E402
import noise_area_study as nas  # noqa: E402

from sgt.ops import forward_record  # noqa: E402

# weekdays from 2026-10-26: 19 warm-up sessions (to 11-19), then the forward sessions 11-20, 11-23, 11-24
DAYS = [d.date().isoformat() for d in pd.bdate_range("2026-10-26", "2026-11-24")]
NIGHT = datetime(2026, 11, 25, 2, 16, tzinfo=timezone.utc)          # 21:16 ET on 11-24


@dataclass
class B:
    session_date: date
    minute_of_day: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None
    segment: str


def session(day: str, move: float, base: float = 100.0, n: int = nas.DAY_MIN, peak: bool = False) -> list[B]:
    """A straight drift of ``move`` by the close; ``peak``: up ``move`` by midday and back to the open by the close
    (the VWAP stop of rule B leaves such a day long before rule A would)."""
    half = nas.DAY_MIN / 2
    px = (lambda k: base * (1 + move * (1 - abs(k - half) / half))) if peak else (lambda k: base * (1 + move * k / nas.DAY_MIN))
    return [B(date.fromisoformat(day), nas.OPEN_MIN + i, px(i), max(px(i), px(i + 1)), min(px(i), px(i + 1)), px(i + 1),
              1000.0, (px(i) + px(i + 1)) / 2, "rth") for i in range(n)]


def market(big: float = 0.03) -> list[B]:
    """Quiet sessions (0.4% drifts, alternating), then three forward sessions that trend hard: the rule trades them."""
    out = []
    for i, d in enumerate(DAYS):
        fwd = d >= nf.FIRST
        out += session(d, (big if i % 2 == 0 else -big) if fwd else (0.004 if i % 2 == 0 else -0.004), peak=d == DAYS[-1])
    return out


def fetcher(bars, calls=None):
    def fetch(symbol, start, end):
        if calls is not None:
            calls.append((symbol, start, end))
        return [b for b in bars if start.date() <= b.session_date <= end.date()]
    return fetch


def test_it_records_each_forward_session_once_with_the_studys_own_numbers(tmp_path):
    calls = []
    res = nf.record(NIGHT, fetcher(market(), calls), tmp_path)
    led = nf.ledger(tmp_path)
    assert res["added"] == 6 and res["until"] == "2026-11-24" and res["sessions"] == {"SPY": 3, "QQQ": 3}
    assert sorted(led.session.unique()) == ["2026-11-20", "2026-11-23", "2026-11-24"] and (led.trades >= 1).all()
    assert [c[0] for c in calls] == ["SPY", "QQQ"] and all(c[1].tzinfo is not None for c in calls)
    s = nas.sessions_from_bars(nf.frame(market()))
    whole = nas.simulate(s, rule="B", fill="next", cost=nas.COSTS["careful"], start=nf.FIRST)       # the study's run
    mine = led[led.symbol == "SPY"].sort_values("session")
    assert list(mine.trades) == list(whole.trades) and np.allclose(mine.gross, whole.gross, atol=2e-4)
    assert np.allclose(mine.ret, whole.ret, atol=2e-4) and (mine.ret < mine.gross).all()             # costs are paid
    other = nas.simulate(s, rule="A", fill="next", cost=nas.COSTS["careful"], start=nf.FIRST)       # not the registered rule
    assert not np.allclose(other.ret, whole.ret, atol=2e-4)
    again = nf.record(NIGHT, fetcher(market(), calls), tmp_path)
    assert again["added"] == 0 and len(calls) == 2 and nf.ledger(tmp_path).equals(led)               # no request, no rewrite


def test_nothing_before_the_first_forward_session_and_nothing_of_an_unfinished_one(tmp_path):
    calls = []
    before = datetime(2026, 11, 20, 2, 16, tzinfo=timezone.utc)                                      # 21:16 ET on 11-19
    assert nf.record(before, fetcher(market(), calls), tmp_path)["added"] == 0 and calls == []
    midday = datetime(2026, 11, 20, 17, 0, tzinfo=timezone.utc)                                      # 12:00 ET on 11-20
    assert nf.last_complete(midday) == "2026-11-19"
    assert nf.last_complete(datetime(2026, 11, 20, 21, 19, tzinfo=timezone.utc)) == "2026-11-19"     # 16:19 ET: not settled
    assert nf.last_complete(datetime(2026, 11, 20, 21, 20, tzinfo=timezone.utc)) == "2026-11-20"
    res = nf.record(datetime(2026, 11, 21, 2, 16, tzinfo=timezone.utc), fetcher(market()), tmp_path)
    assert res["sessions"] == {"SPY": 1, "QQQ": 1}
    noon = datetime(2026, 11, 23, 17, 0, tzinfo=timezone.utc)                                        # 12:00 ET on 11-23:
    assert nf.record(noon, fetcher(market()), tmp_path)["added"] == 0                                # its bars exist, it is not over
    assert set(nf.ledger(tmp_path).session) == {"2026-11-20"}
    more = nf.record(NIGHT, fetcher(market()), tmp_path)
    assert more["added"] == 4 and nf.ledger(tmp_path).groupby("symbol").session.nunique().to_dict() == {"QQQ": 3, "SPY": 3}


def test_bad_vendor_rows_are_dropped_and_a_missing_fetch_adds_nothing(tmp_path):
    good = session("2026-11-20", 0.01)[:3]
    bad = [B(date(2026, 11, 20), 600, float("nan"), 1, 1, 1, 10, None, "rth"), B(date(2026, 11, 20), 601, 1, 1, 1, -5, 10, None, "rth"),
           B(date(2026, 11, 20), 602, "x", 1, 1, 1, 10, None, "rth")]
    f = nf.frame(good + bad)
    assert len(f) == 3 and list(f.columns)[:2] == ["session_date", "minute_of_day"]
    assert nf.record(NIGHT, lambda *a: [], tmp_path)["added"] == 0 and not (tmp_path / "ledger.csv").exists()


def test_the_decision_is_made_once_at_the_fixed_count_on_the_first_sessions_only(tmp_path):
    days = [d.date().isoformat() for d in pd.bdate_range("2026-11-20", periods=nf.DECIDE_AT + 5)]
    rng = np.random.default_rng(7)
    mk = lambda sym, mean: pd.DataFrame({"symbol": sym, "session": days, "ret": rng.normal(mean, 0.004, len(days)),  # noqa: E731
                                         "gross": 0.0, "trades": 1, "wins": 0, "recorded_at": "x"})
    led = pd.concat([mk("SPY", -0.0005), mk("QQQ", 0.002)], ignore_index=True)
    assert nf.decide(led[led.session < days[nf.DECIDE_AT - 1]], tmp_path) is None and not (tmp_path / "result.json").exists()
    res = nf.decide(led, tmp_path, NIGHT)
    assert res["alpha"] == pytest.approx(0.025) and res["plan"] == nf.PLAN
    assert res["by"]["QQQ"]["verdict"] == "PASS" and res["by"]["SPY"]["verdict"] == "CLOSED"
    assert res["by"]["QQQ"]["sessions"] == nf.DECIDE_AT
    x = led[led.symbol == "QQQ"].ret.to_numpy(float)[:nf.DECIDE_AT]
    assert res["by"]["QQQ"]["lower_bound"] == nas.lower_bound(x, alpha=0.025) != nas.lower_bound(x, alpha=0.05)
    assert res["by"]["QQQ"]["mean_daily"] == pytest.approx(led[led.symbol == "QQQ"].ret.iloc[:nf.DECIDE_AT].mean())
    led.loc[led.symbol == "QQQ", "ret"] = -1.0                                                       # later data never changes it
    assert nf.decide(led, tmp_path) == json.loads((tmp_path / "result.json").read_text()) == res


def test_the_summary_line_reads_the_ledger_and_survives_a_bad_file(tmp_path):
    p = tmp_path / "ledger.csv"
    assert forward_record.noise_area_line(p) is None
    p.write_text("symbol,session,ret\nSPY,2026-11-20,0.01\nSPY,2026-11-23,-0.004\nQQQ,2026-11-20,0.002\nQQQ,2026-11-23,oops\nQQQ,2026-11-24,nan\n")
    assert forward_record.noise_area_line(p) == ("trend rule (record only, no trades): QQQ 1 sessions, sum +0.20%, avg +0.200%/day · "
                                                 "SPY 2 sessions, sum +0.60%, avg +0.300%/day; decided at 250 sessions")
    p.write_bytes(b"\xff\xfe\x00garbage")
    assert forward_record.noise_area_line(p) is None
    p.write_text("symbol,session\nSPY,2026-11-20\n")
    assert forward_record.noise_area_line(p) is None


def test_the_plan_the_job_and_the_rule_are_the_frozen_ones():
    root = Path(__file__).resolve().parents[2]
    assert (root / nf.PLAN).is_file() and nf.SYMBOLS == ("SPY", "QQQ") and nf.FIRST == "2026-11-20" and nf.DECIDE_AT == 250
    assert "noiseforward) run reports/noise_area_forward/run.log python /app/scripts/noise_area_forward.py record" in (
        root / "deploy/fly/entrypoint.sh").read_text()
    assert "job noiseforward" in (root / "deploy/fly/crontab").read_text()
    assert nas.COSTS["careful"] == 0.02 and (nas.LOOKBACK, nas.STEP) == (14, 30)
