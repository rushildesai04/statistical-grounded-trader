"""Adversarial pass 4 (red team) reproducers. Read-only against production code."""
from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from sgt.megacap import live
from sgt.megacap.daily import DAILY_FEATURES

ET = ZoneInfo("America/New_York")


def _features(n=400):
    rng = np.random.default_rng(1)
    sess = [d.date() for d in pd.bdate_range("2025-01-02", periods=n)]
    df = pd.DataFrame({f: rng.normal(size=n) for f in DAILY_FEATURES})
    df["session"], df["label"] = sess, rng.normal(0, 0.01, n)
    return df


def test_p4_1_slow_fetch_decision_written_after_1555_is_void(tmp_path, monkeypatch):
    """Prereg: a decision written after 15:55 ET is void. do_decide captures now BEFORE the Alpaca fetches and
    decide() (since 4a34bb7) stamps that instant, so a decision persisted at 15:57 ET is stamped 15:51 and booked."""
    import megacap_forward as mf

    clock = [datetime(2026, 11, 9, 15, 51, 0, tzinfo=ET)]           # Monday session; ext set starts after orig

    class FakeDT(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

    def slow_bars(symbols, *a, **k):
        clock[0] = datetime(2026, 11, 9, 15, 57, 0, tzinfo=ET)      # degraded Alpaca: 6 minutes of paging
        return {s: pd.DataFrame({"ts": pd.Series([], dtype="datetime64[ns, UTC]")}) for s in symbols}

    monkeypatch.setattr(mf, "datetime", FakeDT)
    monkeypatch.setattr(mf, "alpaca_bars", slow_bars)
    monkeypatch.setattr(mf, "adjusted_daily", lambda end, cfg: {})
    monkeypatch.setattr(live, "today_row", lambda *a, **k: _features(1).iloc[0])
    live.train_month("2026-11", {s: _features() for s in live.TICKERS}, root=tmp_path)
    cfg = dataclasses.replace(mf.SETS["orig"], root=tmp_path)

    try:
        mf.do_decide(cfg)
    except live.LiveError:
        return                                                      # refusing the late write is correct
    written_wall = clock[0]                                         # the file hit disk at 15:57 ET
    assert (written_wall.hour, written_wall.minute) > live.VOID_AFTER
    daily = {s: pd.DataFrame({"close": [100.0, 101.0]}, index=[date(2026, 11, 9), date(2026, 11, 10)])
             for s in live.TICKERS}
    m = live.mark(daily, root=tmp_path)
    stamp = json.loads((tmp_path / "decisions" / "2026-11-09.json").read_text())["written_at"]
    assert bool(m.void.all()), (f"decision persisted at {written_wall:%H:%M} ET was stamped {stamp} and booked as "
                                "valid; prereg voids decisions written after 15:55 ET")


def test_p4_2_fade_paper_trade_never_opens_after_an_early_close(tmp_path):
    """Black Friday 2026-11-27 closes 13:00 ET. A 14:00 ET alert passes the fixed 09:30-15:44 window and the paper
    book opens (and texts) a short on a post-close print. Mirror of closed scanner F-12, in the fade book."""
    from datetime import timedelta, timezone

    from sgt.fast import fade
    from sgt.ops import approvals as ap
    from sgt.ops.store import OpsStore

    db = OpsStore(tmp_path / "ops.db")
    posted = datetime(2026, 11, 27, 13, 59, 50, tzinfo=ET).astimezone(timezone.utc)
    ap.arm(db, posted)
    res = fade.on_alert(db, fade.LiveAlert("ABC", "volume_spike", posted, "-1", 1, None), now=posted)
    sent = []
    quote = lambda sym, **k: 5.0  # noqa: E731
    fade.monitor(db, sent.append, quote, now=posted + timedelta(seconds=15))
    with db.tx() as c:
        st = [r[0] for r in c.execute("SELECT status FROM fade_trades")]
    assert "open" not in st and not any("SHORT" in t for t in sent), (
        f"on_alert={res!r}; statuses={st}; a paper fade short opened 1 h after the 13:00 early close; texts={sent}")


def test_p4_1_decide_writes_late_naive_clock_refused(tmp_path, monkeypatch):
    live.train_month("2026-11", {s: _features() for s in live.TICKERS}, root=tmp_path)
    monkeypatch.setattr(live, "today_row", lambda *a, **k: _features(1).iloc[0])
    import pytest
    with pytest.raises(live.LiveError, match="timezone-aware"):
        live.decide(date(2026, 11, 9), datetime(2026, 11, 9, 15, 50, tzinfo=ET), {}, {}, root=tmp_path,
                    clock=lambda: datetime(2026, 11, 9, 15, 51))
    assert not (tmp_path / "decisions" / "2026-11-09.json").exists()


def test_p4_1_model_fill_agrees_with_the_void_rule():
    """Defense in depth: the book refuses what mark() voids (written after 15:55, another day, or no stamp)."""
    import sms_control as sc
    d, at = date(2026, 11, 9), datetime(2026, 11, 9, 15, 58, tzinfo=ET)
    dec = lambda h, m, day=9: {"date": "2026-11-09", "mode": "realtime",  # noqa: E731
                               "written_at": datetime(2026, 11, day, h, m, tzinfo=ET).isoformat()}
    assert sc.model_fill(dec(15, 55), at) == d
    assert sc.model_fill(dec(15, 56), at) is None                   # written after 15:55 -> void -> not traded
    assert sc.model_fill(dec(15, 50, day=6), at) is None            # stale stamp
    assert sc.model_fill({"date": "2026-11-09", "mode": "realtime"}, at) is None          # no stamp: fail closed
    assert sc.model_fill({**dec(15, 50), "written_at": "garbage"}, at) is None
    assert sc.MODEL_VOID_AFTER == live.VOID_AFTER


def test_p4_3_model_fill_uses_the_early_close():
    """Black Friday 2026-11-27 closes 13:00: the 15:50 run's decision must not book at a close already printed."""
    import sms_control as sc
    d = date(2026, 11, 27)
    w = lambda h, m: {"date": d.isoformat(), "mode": "realtime",  # noqa: E731
                      "written_at": datetime(2026, 11, 27, h, m, tzinfo=ET).isoformat()}
    assert sc.model_fill(w(15, 50), datetime(2026, 11, 27, 15, 51, tzinfo=ET)) is None
    assert sc.model_fill(w(12, 50), datetime(2026, 11, 27, 13, 5, tzinfo=ET)) is None     # after the 13:00 bell
    assert sc.model_fill(w(12, 50), datetime(2026, 11, 27, 12, 51, tzinfo=ET)) == d        # before it: trades
    assert sc.model_fill({**w(15, 50), "date": "2026-11-26"}, datetime(2026, 11, 26, 15, 51, tzinfo=ET)) is None


def test_p4_2_entry_window_follows_the_calendar_close():
    from sgt.fast import fade
    assert fade.last_entry(date(2026, 11, 9)).strftime("%H:%M") == "15:44"       # normal day unchanged
    assert fade.last_entry(date(2026, 11, 27)).strftime("%H:%M") == "12:44"      # 13:00 close


def test_p4_2_pending_fade_never_opens_after_the_bell(tmp_path):
    """An alert inside the early-close window whose entry is delayed past 13:00 is skipped, not opened."""
    from datetime import timedelta, timezone

    from sgt.fast import fade
    from sgt.ops import approvals as ap
    from sgt.ops.store import OpsStore

    db = OpsStore(tmp_path / "ops.db")
    posted = datetime(2026, 11, 27, 12, 43, 40, tzinfo=ET).astimezone(timezone.utc)   # entry bar 12:44
    ap.arm(db, posted)
    assert fade.on_alert(db, fade.LiveAlert("ABC", "volume_spike", posted, "-1", 1, None), now=posted) != "filtered: window"
    sent = []
    fade.monitor(db, sent.append, lambda sym, **k: None, now=posted + timedelta(seconds=30))   # no print yet
    fade.monitor(db, sent.append, lambda sym, **k: 5.0, now=datetime(2026, 11, 27, 13, 0, 5, tzinfo=ET))
    with db.tx() as c:
        rows = [tuple(r) for r in c.execute("SELECT status, reason FROM fade_trades")]
    assert rows == [("skipped", "session closed before entry")] and not any("SHORT" in t for t in sent)
