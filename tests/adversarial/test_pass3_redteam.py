"""Adversarial pass 3 (red team), F-13: decide() must stamp written_at from the clock its void guard checked."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sgt.megacap import live
from sgt.megacap.daily import DAILY_FEATURES

ET = ZoneInfo("America/New_York")


def _features(n=400):
    rng = np.random.default_rng(1)
    sess = [d.date() for d in pd.bdate_range("2025-01-02", periods=n)]
    df = pd.DataFrame({f: rng.normal(size=n) for f in DAILY_FEATURES})
    df["session"], df["label"] = sess, rng.normal(0, 0.01, n)
    return df


def _write(root, d, written_at, pos):
    p = root / "decisions" / f"{d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": str(d), "written_at": written_at.astimezone(timezone.utc).isoformat(),
                             "mode": "realtime", "model_month": "2026-11",
                             "positions": {s: {"position": v} for s, v in pos.items()}}))


def test_mark_voids_only_decisions_stamped_after_1555(tmp_path):
    d, nxt = date(2026, 11, 9), date(2026, 11, 10)
    daily = {"XOM": pd.DataFrame({"close": [100.0, 101.0]}, index=[d, nxt])}
    _write(tmp_path / "ok", d, datetime(2026, 11, 9, 15, 50, tzinfo=ET), {"XOM": 1})
    assert not bool(live.mark(daily, root=tmp_path / "ok").void.any())
    _write(tmp_path / "late", d, datetime(2026, 11, 9, 16, 30, tzinfo=ET), {"XOM": 1})
    m = live.mark(daily, root=tmp_path / "late")
    assert bool(m.void.all()) and int(m.position.iloc[0]) == 0


def test_decide_stamps_the_clock_its_guard_checked(tmp_path, monkeypatch):
    live.train_month("2026-11", {s: _features() for s in live.TICKERS}, root=tmp_path)
    monkeypatch.setattr(live, "today_row", lambda *a, **k: _features(1).iloc[0])
    now_et = datetime(2026, 11, 9, 15, 50, 15, tzinfo=ET)
    p = live.decide(date(2026, 11, 9), now_et, {}, {}, tickers=live.TICKERS, root=tmp_path)
    stamp = pd.Timestamp(json.loads(p.read_text())["written_at"])
    assert stamp.to_pydatetime() == now_et                         # whatever the host clock says
    daily = {s: pd.DataFrame({"close": [100.0, 101.0]}, index=[date(2026, 11, 9), date(2026, 11, 10)])
             for s in live.TICKERS}
    assert not bool(live.mark(daily, root=tmp_path).void.any())    # a 15:50 decision is never booked flat


def test_decide_refuses_a_naive_clock(tmp_path, monkeypatch):
    live.train_month("2026-11", {s: _features() for s in live.TICKERS}, root=tmp_path)
    with pytest.raises(live.LiveError, match="timezone-aware"):
        live.decide(date(2026, 11, 9), datetime(2026, 11, 9, 15, 50), {}, {}, root=tmp_path)
    assert not (tmp_path / "decisions").exists()
