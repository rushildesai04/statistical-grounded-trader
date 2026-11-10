"""Amendment F7 (owner 2026-11-10, option A "longs first"): no short on a beta >= 3 name; unknown never blocks."""
from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import fade
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

ET = ZoneInfo("America/New_York")
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = OpsStore(tmp_path / "ops.db")
    ap.arm(d, T10)
    return d


def alert(sym="ABCD"):
    return fade.LiveAlert(sym, "volume_spike", T10 - timedelta(seconds=10), "-1", 1, None)


def row(db):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM fade_trades ORDER BY id DESC LIMIT 1").fetchone())


def run(db, beta, news=None):
    sent, calls = [], []

    def check(sym, at):
        calls.append(sym)
        return beta
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, sent.append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20), news=news, beta=check)
    return sent, calls


@pytest.mark.parametrize("b", [3.0, 3.6])
def test_high_beta_is_not_shorted(db, b):
    sent, calls = run(db, b)
    r = row(db)
    assert (r["status"], r["reason"], r["beta"]) == ("filtered", fade.BETA_REASON, b)
    assert sent == [] and calls == ["ABCD"]


@pytest.mark.parametrize("b", [2.99, 0.4])
def test_lower_beta_trades_and_is_stamped(db, b):
    sent, _ = run(db, b)
    r = row(db)
    assert (r["status"], r["beta"]) == ("open", b) and len(sent) == 1


def test_unknown_beta_never_blocks(db):
    sent, _ = run(db, None)
    r = row(db)
    assert r["status"] == "open" and r["beta"] is None and len(sent) == 1


def test_company_news_is_checked_first(db):
    sent, calls = run(db, 5.0, news=lambda s, at: {"news_own": True, "news_earn": False, "headline": "x"})
    r = row(db)
    assert r["reason"] == fade.NEWS_REASON and calls == [] and r["beta"] is None


def test_without_a_beta_check_the_book_is_unchanged(db):
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, [].append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20))
    assert row(db)["status"] == "open"


def test_entry_beta_budget_and_cache(db, monkeypatch):
    import fade_live as fl
    import numpy as np
    import pandas as pd
    from datetime import date

    def frames(syms):
        rng = np.random.default_rng(1)
        m = rng.normal(0, 0.01, 200)
        idx = [date(2025, 1, 2) + timedelta(days=i) for i in range(201)]

        def mk(r):
            return pd.DataFrame({"close": 100 * np.cumprod(np.r_[1, 1 + r]), "high": 1.0, "low": 1.0}, index=idx)
        return {"SPY": mk(m), "ABCD": mk(3.3 * m)}
    b = fl.entry_beta(db, "ABCD", T10, fetch=frames)
    assert b == pytest.approx(3.3, abs=0.05)
    assert fl.entry_beta(db, "ABCD", T10, fetch=lambda s: pytest.fail("cached")) == b

    def slow(syms):
        time.sleep(1.0)
        return frames(syms)
    monkeypatch.setattr(fl, "BETA_BUDGET", 0.2)
    t0 = time.monotonic()
    assert fl.entry_beta(db, "SLOW", T10, fetch=slow) is None and time.monotonic() - t0 < 0.8   # budget, not the fetch


def test_auto_entry_after_an_unanswered_ask_is_gated_too(db, monkeypatch):
    """Review F-2: the ask path's automatic entry at the deadline applies F6/F7 like the armed path."""
    monkeypatch.setattr(fade, "ASK_FIRST", True)
    with db.tx() as c:
        c.execute("DELETE FROM flags WHERE key='fast_armed'")                 # not armed: the ask path runs
    sent = []
    fade.on_alert(db, alert(), now=T10)
    assert row(db)["status"] == "ask"
    fade.monitor(db, sent.append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=1))      # sends the ask
    fade.monitor(db, sent.append, lambda s, **k: 4.0, now=T10 + timedelta(minutes=4, seconds=21),
                 beta=lambda s, at: 4.2)                                                    # deadline: auto-entry
    r = row(db)
    assert (r["status"], r["reason"], r["beta"]) == ("filtered", fade.BETA_REASON, 4.2)
