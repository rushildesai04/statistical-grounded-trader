"""Amendment F7 (owner 2026-11-10, option A "longs first"): no short on a beta >= 3 name. Unknown beta: no short
(owner 2026-11-21; it never blocked before)."""
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


def test_unknown_beta_is_no_short_and_is_read_again_until_the_entry_window_ends(db):
    """Owner 2026-11-21 (audit finding 12): no beta, no short, like unknown borrow. Until then it never blocked."""
    sent, calls = run(db, None)                                   # 20 s after the alert: still inside the window
    r = row(db)
    assert r["status"] == "pending" and r["beta"] is None and sent == [] and calls == ["ABCD"]
    seen = []
    fade.monitor(db, sent.append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=40),
                 beta=lambda sym, at: seen.append(sym))          # still unknown: read again, still no short
    assert seen == ["ABCD"] and row(db)["status"] == "pending"
    fade.monitor(db, sent.append, lambda s, **k: 4.0, now=T10 + timedelta(minutes=5, seconds=1), beta=lambda sym, at: None)
    r = row(db)
    assert (r["status"], r["reason"], r["beta"]) == ("filtered", fade.BETA_UNKNOWN, None) and sent == []


def test_a_beta_that_arrives_inside_the_window_lets_the_short_through(db):
    sent, _ = run(db, None)
    fade.monitor(db, sent.append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=40), beta=lambda sym, at: 1.2)
    r = row(db)
    assert (r["status"], r["beta"]) == ("open", 1.2) and len(sent) == 1


def test_no_print_and_price_reasons_come_before_unknown_beta(db):
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, [].append, lambda s, **k: None, now=T10 + timedelta(minutes=5, seconds=1), beta=lambda sym, at: None)
    assert row(db)["reason"] == fade.NO_PRINT_REASON             # halted or no quote: not an event, as before


def test_price_outside_the_band_keeps_its_reason_when_beta_is_unknown(tmp_path):
    d = OpsStore(tmp_path / "p.db")
    ap.arm(d, T10)
    fade.on_alert(d, alert(), now=T10)
    fade.monitor(d, [].append, lambda s, **k: 0.6, now=T10 + timedelta(seconds=20), beta=lambda sym, at: None)
    with d.tx() as c:
        assert c.execute("SELECT reason FROM fade_trades").fetchone()[0].startswith("price ")


def test_unknown_beta_is_an_event_the_long_book_and_the_repeat_rule_still_see():
    from sgt.fast import long_shadow

    assert fade.BETA_UNKNOWN in fade.EVENT_FILTERS and fade.BETA_UNKNOWN in long_shadow.SHORT_SKIPS
    assert not fade.BETA_UNKNOWN.startswith(fade.BETA_REASON[:9])    # never counted in the "beta >= 3" gate's check


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


def test_short_text_shows_the_beta_split(db):
    """Owner 2026-11-10: the confidence must mean something; the stock's beta and past shorts at that beta are shown."""
    sent, _ = run(db, 1.2)
    assert "📐 β 1.2 · past no-news shorts at β <1.5: win 61%, avg +3.1% (662)" in sent[0]
    from sgt.ops.sms_style import italic
    assert italic("(split of past data, not a tested rule)") in sent[0]


def test_beta_line_buckets_and_unknown():
    from sgt.fast import fade_texts as ft
    assert ft.beta_line(None) is None
    assert "β 1.5-3: win 55%, avg +1.1% (267)" in ft.beta_line(2.0)
    assert "β >=3: win 21%, avg -6.7% (29)" in ft.beta_line(3.0)


def test_a_short_taken_without_a_beta_check_has_no_beta_line(db):
    """An unknown beta is no short since 2026-11-21, so a text without a beta exists only where no check runs."""
    sent = []
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, sent.append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20))
    assert len(sent) == 1 and "📐" not in sent[0]
