"""Forward-test ledger rules: append-only, void-after-15:55, fills at closes (prereg forward)."""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sgt.megacap import live
from sgt.megacap.daily import DAILY_FEATURES

ET = ZoneInfo("America/New_York")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    return tmp_path


def _features(n=400):
    rng = np.random.default_rng(0)
    sess = [d.date() for d in pd.bdate_range("2025-01-02", periods=n)]
    df = pd.DataFrame({f: rng.normal(size=n) for f in DAILY_FEATURES})
    df["session"], df["label"] = sess, rng.normal(0, 0.01, n)
    return df


def test_train_month_uses_only_earlier_rows_and_is_append_only(root):
    feats = {s: _features() for s in live.TICKERS}
    meta = live.train_month("2026-03", feats)
    assert all(m["last_session"] < "2026-03-01" for m in meta["tickers"].values())
    with pytest.raises(live.LiveError, match="already exist"):
        live.train_month("2026-03", feats)


def test_decide_append_only_and_late_void(root, monkeypatch):
    live.train_month("2026-03", {s: _features() for s in live.TICKERS})
    row = _features(1).iloc[0]
    monkeypatch.setattr(live, "today_row", lambda *a, **k: row)
    t = date(2026, 3, 18)
    p = live.decide(t, datetime(2026, 3, 18, 15, 50, tzinfo=ET), {}, {})
    dec = json.loads(p.read_text())
    assert set(dec["positions"]) == set(live.TICKERS)
    assert all(v["position"] in (-1, 0, 1) for v in dec["positions"].values())
    with pytest.raises(live.LiveError, match="append-only"):
        live.decide(t, datetime(2026, 3, 18, 15, 50, tzinfo=ET), {}, {})
    with pytest.raises(live.LiveError, match="void"):
        live.decide(date(2026, 3, 19), datetime(2026, 3, 19, 15, 56, tzinfo=ET), {}, {})


def test_mark_fills_at_closes_and_voids_late_files(root):
    (root / "decisions").mkdir()
    pos = {s: {"position": 0} for s in live.TICKERS} | {"AAPL": {"position": 1}, "MSFT": {"position": -1}}
    on_time = datetime(2026, 3, 18, 15, 50, 5, tzinfo=ET).isoformat()
    late = datetime(2026, 3, 19, 16, 10, tzinfo=ET).isoformat()
    (root / "decisions" / "2026-03-18.json").write_text(json.dumps({"date": "2026-03-18", "written_at": on_time, "positions": pos}))
    (root / "decisions" / "2026-03-19.json").write_text(json.dumps({"date": "2026-03-19", "written_at": late, "positions": pos}))
    idx = [date(2026, 3, 18), date(2026, 3, 19), date(2026, 3, 20)]
    daily = {s: pd.DataFrame({"close": [100.0, 102.0, 101.0]}, index=idx) for s in live.TICKERS}
    m = live.mark(daily).set_index(["date", "symbol"])
    assert m.loc[(date(2026, 3, 18), "AAPL"), "net"] == pytest.approx(0.02 - 0.0004)
    assert m.loc[(date(2026, 3, 18), "MSFT"), "net"] == pytest.approx(-0.02 - 0.0004)
    assert m.loc[(date(2026, 3, 18), "GOOGL"), "net"] == 0
    assert m.loc[(date(2026, 3, 19), "AAPL"), "void"] and m.loc[(date(2026, 3, 19), "AAPL"), "net"] == 0


def test_batch_mode_not_voided_and_hash_checked(root, monkeypatch):
    live.train_month("2026-03", {s: _features() for s in live.TICKERS})
    monkeypatch.setattr(live, "today_row", lambda *a, **k: _features(1).iloc[0])
    p = live.decide(date(2026, 3, 18), datetime(2026, 4, 2, 21, 0, tzinfo=ET), {}, {}, mode="batch")
    assert json.loads(p.read_text())["mode"] == "batch"
    idx = [date(2026, 3, 18), date(2026, 3, 19)]
    m = live.mark({s: pd.DataFrame({"close": [100.0, 101.0]}, index=idx) for s in live.TICKERS})
    assert not m.void.any()
    (root / "models" / "2026-03" / "AAPL.txt").write_text("tampered")
    with pytest.raises(live.LiveError, match="hash"):
        live.load_models("2026-03")


def test_last_complete_session_waits_for_store_completeness():
    # Regression 2026-10-30: at 17:30 ET the backfill still treats today as trading (post_close 20:00),
    # so catch-up must not try to decide today until post_close + 16 min.
    from megacap_forward import last_complete_session

    assert last_complete_session(datetime(2026, 10, 30, 17, 30, tzinfo=ET)) == date(2026, 10, 29)
    assert last_complete_session(datetime(2026, 10, 30, 20, 15, tzinfo=ET)) == date(2026, 10, 29)
    assert last_complete_session(datetime(2026, 10, 30, 20, 16, tzinfo=ET)) == date(2026, 10, 30)
    # Early close (day after Thanksgiving): post_close 17:00 ET.
    assert last_complete_session(datetime(2026, 11, 27, 17, 16, tzinfo=ET)) == date(2026, 11, 27)
    assert last_complete_session(datetime(2026, 11, 28, 9, 0, tzinfo=ET)) == date(2026, 11, 27)
