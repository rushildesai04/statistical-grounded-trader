"""Plumbing of prereg_intraday_momentum_2026-11-19 on synthetic bars: the rule as the paper states it, no look-ahead,
costs, the gate and the one-shot runs. No SPY or QQQ price is read here."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import intraday_momentum_study as st  # noqa: E402


def day_bars(day: str, path, n: int = st.DAY_MIN, volume: float = 1000.0, skip=()):
    """One session of 1-minute bars. ``path(k)`` = the price at minute k of the session (k = 0 is 09:30): the bar
    starting at minute i opens at path(i) and closes at path(i + 1)."""
    rows = []
    for i in range(n):
        if i in skip:
            continue
        o, c = float(path(i)), float(path(i + 1))
        rows.append({"session_date": day, "minute_of_day": st.OPEN_MIN + i, "open": o, "high": max(o, c), "low": min(o, c),
                     "close": c, "volume": volume, "vwap": (o + c) / 2, "segment": "rth"})
    return rows


def frame(days: list[list[dict]], extra=()) -> pd.DataFrame:
    return pd.DataFrame([r for d in days for r in d] + list(extra))


def ramp(to: float, base: float = 100.0):
    """A straight line from ``base`` at the open to ``base x (1 + to)`` at the close."""
    return lambda k: base * (1 + to * k / st.DAY_MIN)


DAYS = [f"2030-01-{d:02d}" for d in range(1, 29)]


def history(move: float = 0.01, n: int = 14):
    """n sessions that each drift ``move`` up by the close, alternating sign, all opening and closing near 100."""
    return [day_bars(DAYS[i], ramp(move if i % 2 == 0 else -move)) for i in range(n)]


def test_prices_vwap_and_fills_come_from_closed_bars_only():
    bars = frame([day_bars("2030-01-01", lambda k: 100 + k, n=6, skip=(2,))],
                 extra=[{"session_date": "2030-01-01", "minute_of_day": 500, "open": 1, "high": 1, "low": 1, "close": 1,
                         "volume": 9e9, "vwap": 1, "segment": "pre"}])                        # pre-market is never read
    s = st.sessions_from_bars(bars)
    P, V, O = s["P"][0], s["V"][0], s["O"][0]
    assert s["last"][0] == 6 and list(P[:7]) == [100, 101, 102, 102, 104, 105, 106] and np.isnan(P[7])   # minute 3: the last price stands
    assert V[1] == pytest.approx(100.5) and V[2] == pytest.approx((100.5 + 101.5) / 2) and V[3] == V[2] and np.isnan(V[0])
    assert list(O[:6]) == [100, 101, 103, 103, 104, 105]                                       # no bar at minute 2: the next bar's open


def test_sigma_is_the_mean_absolute_move_of_the_previous_14_sessions_and_never_today():
    hist = history(0.01)
    today = day_bars(DAYS[14], ramp(0.05))
    s = st.sessions_from_bars(frame(hist + [today]))
    sigma, upper, lower = st.boundaries(s)
    assert np.isnan(sigma[13]).all()                                                           # 13 sessions before it: none
    assert sigma[14, 390] == pytest.approx(0.01) and sigma[14, 195] == pytest.approx(0.005) and sigma[14, 0] == 0
    wild = st.sessions_from_bars(frame(hist + [day_bars(DAYS[14], ramp(-0.30))]))
    assert np.allclose(st.boundaries(wild)[0][14], sigma[14])                                  # today's path changes nothing
    prev_close = s["P"][13, 390]
    assert upper[14, 390] == pytest.approx(max(100.0, prev_close) * 1.01) and lower[14, 390] == pytest.approx(min(100.0, prev_close) * 0.99)
    gap = st.sessions_from_bars(frame(hist + [day_bars(DAYS[14], ramp(0.0, base=103.0))]))     # gaps up 3-4% over the close
    _, u2, l2 = st.boundaries(gap)
    assert u2[14, 390] == pytest.approx(103.0 * 1.01) and l2[14, 390] == pytest.approx(gap["P"][13, 390] * 0.99)


def test_a_time_needs_ten_of_the_fourteen_sessions_and_a_half_day_gives_its_own_close():
    hist = [day_bars(DAYS[i], ramp(0.01), n=210 if i < 5 else st.DAY_MIN) for i in range(14)]  # five half days
    s = st.sessions_from_bars(frame(hist + [day_bars(DAYS[14], ramp(0.0))]))
    sigma, upper, _ = st.boundaries(s)
    assert np.isfinite(sigma[14, 210]) and np.isnan(sigma[14, 211])                            # 9 of 14 reach minute 211
    half_then_full = st.sessions_from_bars(frame(history(0.01, 13) + [day_bars(DAYS[13], ramp(0.02), n=210), day_bars(DAYS[14], ramp(0.0, base=90.0))]))
    assert half_then_full["last"][13] == 210
    _, u, lo = st.boundaries(half_then_full)
    prev = half_then_full["P"][13, 210]                                                        # the 13:00 close, not minute 390
    assert u[14, 60] == pytest.approx(prev * (1 + st.boundaries(half_then_full)[0][14, 60])) and lo[14, 60] < 90.0


def arrays(P, V=None, U=None, L=None):
    n = st.DAY_MIN + 1
    f = lambda x, d: np.full(n, d, float) if x is None else np.asarray(x, float)  # noqa: E731
    P = np.asarray(P, float)
    return P, f(V, 100.0), P.copy(), f(U, 101.0), f(L, 99.0)


def test_rule_b_is_long_above_band_and_vwap_flat_inside_and_reverses_below():
    P = np.full(st.DAY_MIN + 1, 100.0)
    P[30], P[60], P[90], P[120], P[150] = 102, 102, 100.5, 98, 98                              # long, hold, stopped, short, hold
    got = st.day_trades(*arrays(P), last=390, rule="B", fill="decision")
    assert [(t["side"], t["in_k"], t["out_k"]) for t in got] == [(1, 30, 90), (-1, 120, 180)]  # k=180: back inside -> flat
    assert got[0]["entry"] == 102 and got[0]["exit"] == 100.5 and got[1]["exit"] == 100.0
    P2 = np.full(st.DAY_MIN + 1, 100.0)
    P2[30], P2[60] = 102, 98                                                                   # straight from long to short
    rev = st.day_trades(*arrays(P2), last=390, rule="B", fill="decision")
    assert [(t["side"], t["in_k"], t["out_k"]) for t in rev] == [(1, 30, 60), (-1, 60, 90)]
    above_band_under_vwap = st.day_trades(*arrays(np.full(st.DAY_MIN + 1, 102.0), V=np.full(st.DAY_MIN + 1, 103.0)), last=390, rule="B", fill="decision")
    assert above_band_under_vwap == []                                                         # the stop would take it at once
    late = np.full(st.DAY_MIN + 1, 100.0)
    late[360:] = 103
    last_mark = st.day_trades(*arrays(late), last=390, rule="B", fill="decision")
    assert [(t["in_k"], t["out_k"]) for t in last_mark] == [(360, 390)]                        # 15:30 is the last decision; out at the close
    off = np.full(st.DAY_MIN + 1, 100.0)
    off[45] = 110                                                                              # a spike between two marks is not seen
    assert st.day_trades(*arrays(off), last=390, rule="B", fill="decision") == []
    at_close = np.full(st.DAY_MIN + 1, 100.0)
    at_close[390] = 105                                                                        # the close itself is not a decision time
    assert st.day_trades(*arrays(at_close), last=390, rule="B", fill="decision") == []
    half_close = np.full(st.DAY_MIN + 1, 100.0)
    half_close[210] = 105
    assert st.day_trades(*arrays(half_close), last=210, rule="B", fill="decision") == []
    half = np.full(st.DAY_MIN + 1, 103.0)
    assert [(t["in_k"], t["out_k"]) for t in st.day_trades(*arrays(half), last=210, rule="B", fill="decision")] == [(30, 210)]


def test_rule_a_holds_until_the_opposite_boundary_and_fills_differ_by_mode():
    P = np.full(st.DAY_MIN + 1, 100.0)
    P[30], P[90], P[150] = 102, 100.0, 98
    a = st.day_trades(*arrays(P), last=390, rule="A", fill="decision")
    assert [(t["side"], t["in_k"], t["out_k"]) for t in a] == [(1, 30, 150), (-1, 150, 390)]   # inside the band: still long
    Pn, V, O, U, L = arrays(P)
    O = O + 0.25                                                                               # the next bar opens a quarter higher
    nxt = st.day_trades(Pn, V, O, U, L, last=390, rule="B", fill="next")
    assert nxt[0]["entry"] == pytest.approx(102.25) and st.day_trades(Pn, V, O, U, L, last=390, rule="B", fill="decision")[0]["entry"] == 102
    blind = st.day_trades(Pn, np.full(st.DAY_MIN + 1, np.nan), O, U, L, last=390, rule="B", fill="next")
    assert blind == []                                                                         # no VWAP, no decision


def trending_sample(n_days: int = 6, up: float = 0.03):
    return st.sessions_from_bars(frame(history(0.004) + [day_bars(DAYS[14 + i], ramp(up if i % 2 == 0 else -up)) for i in range(n_days)]))


def test_the_book_pays_every_fill_counts_round_trips_and_sizes_from_the_previous_value():
    s = trending_sample()
    free = st.simulate(s, "B", "decision", 0.0, start=DAYS[14], end=DAYS[19])
    paid = st.simulate(s, "B", "decision", 0.05, start=DAYS[14], end=DAYS[19])
    assert len(free) == 6 and (free.trades == 1).all() and (free.ret > 0).all()               # one trend trade a day, both directions
    assert list(free.longs) == [1, 0, 1, 0, 1, 0]
    shares0 = np.floor(st.START_VALUE / s["P"][14, 0])
    assert free.ret.iloc[0] - paid.ret.iloc[0] == pytest.approx(2 * shares0 * 0.05 / st.START_VALUE)
    assert paid.gross.iloc[0] == pytest.approx(free.ret.iloc[0]) and (paid.wins == 1).all()
    value1 = st.START_VALUE * (1 + paid.ret.iloc[0])
    shares1 = np.floor(value1 / s["P"][15, 0])
    assert free.ret.iloc[1] - paid.ret.iloc[1] == pytest.approx(2 * shares1 * 0.05 / value1 - (free.ret.iloc[1] - free.ret.iloc[1]), rel=0.05)
    before = st.simulate(s, "B", "decision", 0.0, start=DAYS[0], end=DAYS[13])
    assert before.trades.sum() == 0                                                            # no boundary yet: no trade
    assert st.simulate(s, "B", "next", 0.0, start=DAYS[14], end=DAYS[19]).ret.sum() == pytest.approx(free.ret.sum(), rel=0.2)
    lev = st.simulate(s, "B", "decision", 0.0, sized="vol", start=DAYS[14], end=DAYS[19])
    assert lev.ret.abs().sum() > free.ret.abs().sum() and (lev.trades == free.trades).all()    # calm history: leverage above 1


def test_tomorrow_never_changes_today():
    a = trending_sample(6)
    b = st.sessions_from_bars(frame(history(0.004) + [day_bars(DAYS[14 + i], ramp(0.03 if i % 2 == 0 else -0.03)) for i in range(3)]
                                    + [day_bars(DAYS[17 + i], ramp(-0.5)) for i in range(3)]))
    x = st.simulate(a, "B", "next", 0.02, start=DAYS[14], end=DAYS[16])
    y = st.simulate(b, "B", "next", 0.02, start=DAYS[14], end=DAYS[16])
    assert np.allclose(x.ret, y.ret) and list(x.trades) == list(y.trades)


def test_the_bound_gate_and_verdicts():
    rng = np.random.default_rng(0)
    good, flat = rng.normal(0.001, 0.005, 600), rng.normal(0.0, 0.005, 600)
    assert st.lower_bound(good) > 0 and st.lower_bound(flat) < 0 and st.lower_bound(good) == st.lower_bound(good)
    assert st.lower_bound(np.array([0.01, 0.02])) is None
    d = lambda r: pd.DataFrame({"ret": r})  # noqa: E731
    assert st.decide(d(good))["verdict"] == "PASS" and st.decide(d(-good))["verdict"] == "FAIL"
    weak = np.r_[np.full(300, 0.0102), np.full(300, -0.01)]                                    # a positive mean the bound cannot confirm
    assert st.decide(d(weak))["verdict"] == "LEAD"
    assert st.gate({"trades_per_day": 1.8, "total_return": 0.4})["pass"]
    assert not st.gate({"trades_per_day": 3.0, "total_return": 0.4})["pass"] and not st.gate({"trades_per_day": 1.8, "total_return": -0.1})["pass"]
    assert not st.gate({})["pass"]


class _Vault:
    def __init__(self):
        self.unlocked, self.commit = 0, "abc"

    def read(self, store, symbol, start, end):
        return store(symbol, start, end)

    def unlock(self, scorer):
        self.unlocked += 1


def test_each_step_runs_once_and_the_held_out_period_waits_for_the_gate(tmp_path, monkeypatch, capsys):
    import sgt.intraday.store as store_mod
    import sgt.research.vault as vault_mod

    v = _Vault()
    monkeypatch.setattr(vault_mod, "Vault", lambda study: v)
    monkeypatch.setattr(store_mod, "ParquetBarStore", lambda path: None)
    calls = []
    monkeypatch.setattr(st, "replicate", lambda vault, store, out: calls.append("rep") or (out.mkdir(parents=True, exist_ok=True), (out / "result_replication.json").write_text(json.dumps({"gate": {"pass": False}})), {"gate": {"pass": False}, "paper_setting": {}})[2])
    monkeypatch.setattr(st, "score", lambda vault, store, out: calls.append("score") or ((out / "result_oos.json").write_text("{}"), {"SPY": {"decision": {}}, "QQQ": {"robustness_only": {}}})[1])
    out = tmp_path / "o"
    assert st.main(["score", "--out", str(out)]) == 2 and calls == []                           # no gate result yet
    assert st.main(["replicate", "--out", str(out)]) == 0 and st.main(["replicate", "--out", str(out)]) == 2 and calls == ["rep"]
    assert st.main(["score", "--out", str(out)]) == 2 and calls == ["rep"]                      # the gate failed: stays closed
    (out / "result_replication.json").write_text(json.dumps({"gate": {"pass": True}}))
    assert st.main(["score", "--out", str(out)]) == 0 and st.main(["score", "--out", str(out)]) == 2 and calls == ["rep", "score"]
    assert "opened only after the replication gate" in capsys.readouterr().out


def test_the_study_is_in_the_vault_with_the_frozen_plan():
    from sgt.research.vault import load_registry

    root = Path(__file__).resolve().parents[2]
    s = load_registry(root / "config/holdout_registry.json")[st.STUDY]
    assert s.holdout == (st.OOS_FROM, st.OOS_TO) and s.train[1] == st.REPL_TO and s.prereg == st.PREREG and (root / st.PREREG).is_file()
    assert st.COSTS == {"paper": 0.0045, "careful": 0.02} and (st.LOOKBACK, st.MIN_DAYS, st.STEP, st.BLOCK, st.ALPHA) == (14, 10, 30, 5, 0.05)
