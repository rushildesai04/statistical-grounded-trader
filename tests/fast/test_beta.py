"""Beta ground rule (owner 2026-11-10): beta >= 3 names first in the scanner funnel; beta stored and shown."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sgt.fast import beta, scanner
from sgt.ops.store import OpsStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def daily(returns, start=100.0):
    idx = [date(2025, 1, 2) + timedelta(days=i) for i in range(len(returns) + 1)]
    close = start * np.cumprod(np.concatenate([[1.0], 1 + np.asarray(returns)]))
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e6},
                        index=idx)


RNG = np.random.default_rng(7)
MKT = RNG.normal(0, 0.01, 260)
FRAMES = {"SPY": daily(MKT), "HIGH": daily(3.5 * MKT + RNG.normal(0, 0.002, 260)),
          "LOW": daily(0.8 * MKT + RNG.normal(0, 0.002, 260)), "NEW": daily(3.5 * MKT[-30:])}


def test_compute_raw_beta_and_unknowns():
    out = beta.compute(FRAMES, ["HIGH", "LOW", "NEW", "GONE"])
    assert out["HIGH"][0] == pytest.approx(3.5, abs=0.15) and out["LOW"][0] == pytest.approx(0.8, abs=0.1)
    assert out["NEW"] == (None, 30)                                    # fewer than 60 daily returns
    assert out["GONE"] == (None, 0)
    assert beta.compute({"HIGH": FRAMES["HIGH"]}, ["HIGH"])["HIGH"] == (None, 0)   # no market series


def test_betas_are_cached_per_session_and_failures_retried(db):
    calls = []

    def fetch(syms):
        calls.append(sorted(syms))
        return FRAMES
    b = beta.betas(db, ["HIGH", "LOW"], "2026-11-10", fetch)
    assert b["HIGH"] == pytest.approx(3.5, abs=0.15) and calls == [["HIGH", "LOW", "SPY"]]
    beta.betas(db, ["HIGH", "LOW", "NEW"], "2026-11-10", fetch)
    assert calls[-1] == ["NEW", "SPY"]                                  # only the new symbol is fetched
    assert beta.betas(db, ["HIGH"], "2026-11-10", lambda s: pytest.fail("cached")) == {"HIGH": b["HIGH"]}

    def boom(syms):
        raise RuntimeError("alpaca down")
    assert beta.betas(db, ["LOW", "XYZ"], "2026-11-11", boom) == {"LOW": None, "XYZ": None}
    assert beta.betas(db, ["LOW"], "2026-11-11", fetch)["LOW"] is not None    # not cached as unknown: retried


def test_prioritize_and_label():
    b = {"A": 1.0, "B": 3.2, "C": None, "D": 4.1, "E": 3.0}
    assert beta.prioritize(["A", "B", "C", "D", "E"], b) == ["D", "B", "E", "A", "C"]
    assert (beta.label(3.84), beta.label(1.234), beta.label(None)) == ("β 3.8 (high)", "β 1.2", "β n/a")


def test_ranking_happens_before_the_universe_cap(db, monkeypatch):
    import fade_live as fl
    monkeypatch.setattr(fl, "UNIVERSE_MAX", 3)
    frames = {**FRAMES, "LATE": FRAMES["HIGH"]}
    uni, b = fl.ranked_universe(db, ["LOW", "AAA", "BBB", "CCC", "LATE"], "2026-11-10", lambda s: frames)
    assert uni[0] == "LATE" and len(uni) == 3 and b["LATE"] == pytest.approx(3.5, abs=0.15)


def test_scanner_stores_and_texts_the_beta(db):
    ev = {"bar_ts": pd.Timestamp("2026-11-10 15:00", tz="UTC"), "signal_px": 4.0}
    now = datetime(2026, 11, 10, 15, 0, 30, tzinfo=timezone.utc)
    assert scanner.record(db, "HIGH", ev, now, 3.84)
    sent = []
    scanner.step(db, lambda sym, since=None: 4.1, datetime(2026, 11, 10, 15, 1, 5, tzinfo=timezone.utc), sent.append)
    assert "β 3.8 (high)" in sent[0] and "SCANNER SHORT HIGH" in sent[0]
    with db.tx() as c:
        assert c.execute("SELECT beta FROM scan_trades").fetchone()[0] == 3.84


def test_beta_failure_never_costs_the_stop_checks(db, monkeypatch):
    import fade_live as fl

    def boom(*a, **k):
        raise RuntimeError("db locked")
    monkeypatch.setattr(fl, "ranked_universe", boom)
    monkeypatch.setattr(fl, "scan_universe", lambda client, db, session: ["AAA", "BBB"])
    monkeypatch.setattr(fl, "scan_bars", lambda client, syms, start: {})
    st = fl.ScanState()
    now = datetime(2026, 11, 10, 15, 30, tzinfo=timezone.utc)
    fl._scan_detect(None, db, st, now, now.astimezone(fl.ET))
    assert st.universe == ["AAA", "BBB"] and st.beta == {}


def test_missing_market_frame_is_not_cached(db):
    """Review F-1: a fetch without SPY must not store every symbol as unknown for the rest of the day."""
    no_mkt = {k: v for k, v in FRAMES.items() if k != "SPY"}
    assert beta.betas(db, ["HIGH"], "2026-11-12", lambda s: no_mkt) == {"HIGH": None}
    assert beta.betas(db, ["HIGH"], "2026-11-12", lambda s: FRAMES)["HIGH"] == pytest.approx(3.5, abs=0.15)


def test_scanner_ranking_is_time_budgeted(db, monkeypatch):
    """Review F-3: a slow beta fetch never holds up the scanner; the late result still fills the cache.
    Deterministic (convergence pass): the fetch blocks on an event until the budget has provably passed."""
    import threading
    import time

    import fade_live as fl
    monkeypatch.setattr(fl, "SCAN_BETA_BUDGET", 0.2)
    release = threading.Event()

    def slow(syms):
        release.wait(10)
        return FRAMES
    t0 = time.monotonic()
    uni, b = fl.ranked_universe(db, ["LOW", "HIGH"], "2026-11-13", slow)
    assert time.monotonic() - t0 < 5 and uni == ["LOW", "HIGH"] and b == {}   # returned while the fetch was blocked
    release.set()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:                                       # the background fetch caches both names
        with db.tx() as c:
            if c.execute("SELECT COUNT(*) FROM symbol_beta WHERE session='2026-11-13'").fetchone()[0] == 2:
                break
        time.sleep(0.02)
    uni, b = fl.ranked_universe(db, ["LOW", "HIGH"], "2026-11-13", lambda s: pytest.fail("cached"))
    assert uni == ["HIGH", "LOW"]
