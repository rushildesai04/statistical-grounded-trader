"""The alert's place in the move, labelled nightly from official bars (owner 2026-11-18). A label, never a rule."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.fast import scenario as sc
from sgt.ops.store import OpsStore

POSTED = pd.Timestamp("2026-11-18 15:00:09", tz="UTC")           # 10:00:09 ET -> start bar 15:01
T0 = pd.Timestamp("2026-11-18 15:01", tz="UTC")
NIGHT = datetime(2026, 11, 19, 1, 40, tzinfo=timezone.utc)       # 20:40 ET
MIN = pd.Timedelta(minutes=1)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def day(px_at=7.23, low=5.40, high=7.54, n_before=40):
    """Pre-market and regular bars before the alert (lowest low ``low``, highest high ``high``), the start bar, more."""
    rows = [{"ts": pd.Timestamp("2026-11-18 13:00", tz="UTC"), "open": low + 0.1, "high": low + 0.2, "low": low, "close": low + 0.1,
             "volume": 10, "segment": "pre"}]
    for i in range(n_before - 1):
        rows.append({"ts": T0 - (n_before - 1 - i) * MIN, "open": 6.5, "high": high if i == 5 else 6.6, "low": 6.4, "close": 6.5,
                     "volume": 100, "segment": "rth"})
    rows.append({"ts": T0, "open": px_at, "high": 99.0, "low": 0.01, "close": px_at, "volume": 100, "segment": "rth"})
    rows += [{"ts": T0 + (i + 1) * MIN, "open": 7.2, "high": 7.3, "low": 7.1, "close": 7.2, "volume": 100, "segment": "rth"} for i in range(5)]
    return pd.DataFrame(rows)


def alert(db, sym="CLRO", posted=POSTED, status="filtered", session="2026-11-18"):
    with db.tx() as c:
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                         "weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (sym, session, "volume_increasing", posted.isoformat(), "-1", hash(sym) % 9999, status, None, 0.05,
                          posted.isoformat())).lastrowid


def rows(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT symbol, scen_run, scen_off, scen_band, scen_where, scen_note FROM fade_trades ORDER BY id")]


def test_bands_and_position_use_the_training_cut_points():
    assert [sc.band(x) for x in (0.10, sc.RUN_EDGES[0], 0.34, sc.RUN_EDGES[1], 0.64, 2.0)] == ["early", "early", "middle", "middle", "late", "late"]
    assert sc.where(0.0) == sc.where(-0.02) == "at the high" and sc.where(-0.0201) == "off the high"
    assert sc.RUN_EDGES == pytest.approx((0.3364, 0.6308), abs=1e-4)


def test_state_is_from_bars_before_the_start_bar_only():
    s = sc.state(day(), T0)
    assert s["run"] == pytest.approx(7.23 / 5.40 - 1) and s["off"] == pytest.approx(7.23 / 7.54 - 1)     # 99.0 / 0.01 ignored
    assert sc.state(day(n_before=4), T0) is None and sc.state(day(), T0 + 30 * MIN) is None


def test_start_bar_is_the_first_regular_bar_ten_seconds_after_the_alert_within_five_minutes():
    d = day()
    assert sc.start_bar(d, POSTED) == T0
    assert sc.start_bar(d, pd.Timestamp("2026-11-18 15:00:55", tz="UTC")) == T0 + MIN          # 15:01 is under 10 s away
    gap = d[(d.ts < T0) | (d.ts > T0 + 5 * MIN)]
    assert sc.start_bar(gap, POSTED) is None                                                    # halted: no bar in 5 min
    pre = pd.Timestamp("2026-11-18 13:00:00", tz="UTC")
    assert sc.start_bar(d[d.ts < T0 - 30 * MIN], pre) is None


def test_tag_labels_once_from_official_bars(db):
    alert(db)
    calls = []

    def bars(sym, session):
        calls.append((sym, session))
        return day()
    assert sc.tag(db, bars, NIGHT) == ["CLRO middle, off the high"]
    (r,) = rows(db)
    assert r["scen_band"] == "middle" and r["scen_where"] == "off the high" and r["scen_run"] == pytest.approx(7.23 / 5.40 - 1)
    assert sc.tag(db, bars, NIGHT) == [] and len(calls) == 1


def test_nothing_is_labelled_before_the_official_bars_are_complete(db):
    alert(db)
    early = datetime(2026, 11, 18, 21, 10, tzinfo=timezone.utc)                # 16:10 ET
    assert sc.tag(db, lambda s, d: day(), early) == [] and rows(db)[0]["scen_band"] is None
    assert sc.tag(db, lambda s, d: day(), NIGHT) != []


def test_alerts_that_cannot_be_placed_get_a_note_and_missing_bars_are_retried(db):
    alert(db, "PRE", posted=pd.Timestamp("2026-11-18 13:00:00", tz="UTC"))          # before the open: no start bar
    alert(db, "THIN")
    alert(db, "GONE")
    frames = {"PRE": day(), "THIN": day(n_before=3), "GONE": day().iloc[:0]}
    assert sc.tag(db, lambda s, d: frames[s], NIGHT) == []
    got = {r["symbol"]: r["scen_note"] for r in rows(db)}
    assert got == {"PRE": sc.NO_START, "THIN": sc.FEW_BARS, "GONE": None}
    frames["GONE"] = day(px_at=6.0)
    assert sc.tag(db, lambda s, d: frames[s], NIGHT) == ["GONE early, off the high"]


def test_one_failing_alert_never_blocks_the_others(db):
    alert(db, "BAD")
    alert(db, "GOOD")

    def bars(sym, session):
        if sym == "BAD":
            raise RuntimeError("boom")
        return day()
    assert sc.tag(db, bars, NIGHT) == ["GOOD middle, off the high"]


def test_old_sessions_are_left_alone(db):
    alert(db, session="2026-10-01", posted=pd.Timestamp("2026-10-01 15:00:09", tz="UTC"))
    calls = []
    assert sc.tag(db, lambda s, d: calls.append(s) or day(), NIGHT) == []
    assert rows(db)[0]["scen_band"] is None and rows(db)[0]["scen_note"] is None and calls == []     # not even asked


def test_digest_line_lists_only_alerts_that_became_trades(db):
    alert(db, "SHRT", status="settled")
    alert(db, "SKIP", status="filtered")
    fid = alert(db, "LONG", status="filtered")
    with db.tx() as c:
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, created_at) VALUES (?,?,?,?,?,?)",
                  (fid, "LONG", "2026-11-18", "L4a", "settled", "x"))
    sc.tag(db, lambda s, d: day(px_at=11.0, high=11.1), NIGHT)
    line = sc.line(db, "2026-11-18")
    assert line == ("where each traded alert arrived: SHRT late, at the high (up 104% from the low), "
                    "LONG late, at the high (up 104% from the low)")
    assert sc.line(db, "2026-11-19") is None and sc.count(db) == {"labelled": 3, "no_label": 0}


def test_the_label_is_read_by_no_book():
    from pathlib import Path

    import sgt.fast as pkg
    root = Path(pkg.__file__).parent
    users = [p.name for p in root.glob("*.py") if p.name != "scenario.py" and "scen_" in p.read_text()]
    assert users == []                                                             # a label, not a rule
