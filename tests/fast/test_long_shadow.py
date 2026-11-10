"""Long shadow book (owner 2026-11-10: paper-run the longs): tested setups on real alerts, paper only."""
from __future__ import annotations

import pandas as pd
import pytest

from sgt.fast import long_shadow as ls
from sgt.fast import patterns as pt
from sgt.ops.store import OpsStore

POSTED = pd.Timestamp("2026-11-09 15:00:20", tz="UTC")          # 10:00:20 ET -> start bar 15:01 (alert + 10 s)
T1 = pd.Timestamp("2026-11-09 15:01", tz="UTC")
NOW = pd.Timestamp("2026-11-10 01:40", tz="UTC").to_pydatetime()   # the 20:40 ET settle


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def fade_row(db, sym, status, reason=None, cost=0.01, posted=POSTED):
    with db.tx() as c:
        settled = status == "settled"                 # a settled row always carries its trade weight
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                         " reason, cost_rt, net, weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (sym, "2026-11-09", "volume_spike", posted.isoformat(), "-1", hash(sym) % 10_000, status,
                          reason, cost if settled else None, 0.01 if settled else None, 0.05 if settled else None,
                          posted.isoformat())).lastrowid


def bars(rows, start=T1, pre=()):
    out, t = [], start - len(pre) * pt.BAR
    for o, h, lo, c, v in list(pre) + list(rows):
        out.append({"ts": t, "open": o, "high": h, "low": lo, "close": c, "volume": v})
        t += pt.BAR
    return pd.DataFrame(out)


# start bar 15:01: green, then a red candle at 15:02 (low 10.2, high 10.55), then a rally through 10.55
DAY = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
            (10.65, 11.6, 10.6, 11.5, 800)] + [(11.5, 11.6, 11.4, 11.5, 100)] * 40)


def shadow(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM long_shadow ORDER BY fade_id, kind")]


def test_settled_event_gets_the_tested_setups_once(db):
    fid = fade_row(db, "ABCD", "settled")
    seen = []
    lines = ls.run(db, lambda s, d: (seen.append((s, d)), DAY)[1], lambda *a: pytest.fail("stored cost exists"), now=NOW)
    rows = {r["kind"]: r for r in shadow(db)}
    assert {"L4a", "L4b"} <= set(rows) and seen == [("ABCD", "2026-11-09")]
    a = rows["L4a"]                               # red candle 15:02 -> buy the 15:03 open 10.3, stop 10.2, 2R 10.5
    assert (a["entry"], a["stop"], a["reason"]) == (10.3, 10.2, "target") and a["exit"] == pytest.approx(10.5)
    assert a["net"] == pytest.approx(10.5 / 10.3 - 1 - 0.01) and a["fade_id"] == fid
    b = rows["L4b"]                               # buy-stop at the red high 10.55 fills on 15:03
    assert b["entry"] == 10.55 and b["cost_rt"] == 0.01
    assert any(x.startswith("ABCD L4a ") for x in lines)
    assert ls.run(db, lambda s, d: DAY, lambda *a: 0.0, now=NOW) == [] and len(shadow(db)) == len(rows)   # idempotent


def test_start_bar_must_come_within_five_minutes(db):
    fade_row(db, "LATE", "settled")
    late = bars([(10, 10.5, 10, 10.4, 1)] * 5, start=POSTED.floor("min") + pd.Timedelta(minutes=6))
    ls.run(db, lambda s, d: late, lambda *a: 0.0, now=NOW)
    assert [(r["kind"], r["reason"]) for r in shadow(db)] == [("-", "no start bar")]


def test_halt_event_runs_l7_from_the_reopen_with_modelled_cost(db):
    fade_row(db, "HALT", "void", reason=ls.HALT_REASON)
    fade_row(db, "OUT", "void", reason="outside research filters (window / $1-50)")       # not a research event
    reopen = POSTED.floor("min") + pd.Timedelta(minutes=9)
    day = bars([(10.0, 10.5, 10.0, 10.4, 1), (10.5, 10.55, 10.2, 10.3, 1), (10.3, 11.0, 10.3, 10.9, 1)]
               + [(10.9, 11.0, 10.8, 10.9, 1)] * 40, start=reopen)
    calls = []
    ls.run(db, lambda s, d: day, lambda sym, ts, px: calls.append((sym, px)) or 0.02, now=NOW)
    rows = shadow(db)
    assert {r["kind"] for r in rows} == {"L7a", "L7b"} and all(r["symbol"] == "HALT" for r in rows)
    assert calls and calls[0][0] == "HALT" and all(r["cost_rt"] == 0.02 for r in rows if r["net"] is not None)


def test_short_pays_the_locate_and_no_setup_is_marked(db):
    fade_row(db, "FLAT", "settled")
    flat = bars([(10.0, 10.1, 9.9, 10.05, 1)] * 45)                     # never red: no setup at all
    ls.run(db, lambda s, d: flat, lambda *a: 0.0, now=NOW)
    assert [(r["kind"], r["reason"]) for r in shadow(db)] == [("-", "no setup")]
    pole = [(10.0, 10.2, 10.0, 10.2, 1000), (10.2, 10.5, 10.2, 10.5, 1200), (10.5, 10.8, 10.5, 10.8, 1500),
            (10.8, 11.0, 10.8, 11.0, 1500)]
    flag = [(10.95, 10.98, 10.80, 10.85, 400), (10.85, 10.90, 10.70, 10.75, 300)]
    down = [(10.75, 10.76, 10.5, 10.55, 300), (10.55, 10.6, 10.2, 10.25, 300)] + [(10.25, 10.3, 10.2, 10.25, 1)] * 40
    fade_row(db, "FLAG", "settled", posted=T1 - pd.Timedelta(seconds=10) + 4 * pt.BAR)
    day = bars(flag + down, start=T1 + 4 * pt.BAR, pre=pole)
    ls.run(db, lambda s, d: day, lambda *a: 0.0, now=NOW)
    s2 = [r for r in shadow(db) if r["kind"] == "S2"][0]
    assert s2["side"] == "short" and s2["entry"] == 10.70
    assert s2["net"] == pytest.approx(s2["gross"] - 0.01 - ls.LOCATE)


def test_record_line(db):
    assert ls.record_line(db) is None
    fade_row(db, "ABCD", "settled")
    ls.run(db, lambda s, d: DAY, lambda *a: 0.0, now=NOW)
    line = ls.record_line(db)
    assert line.startswith("long shadow (paper, no orders) ") and "L4a 1 avg " in line and "tested -2.8% to -1.2%" in line


def test_settle_job_survives_a_shadow_failure(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fade_live as fl

    calls = []
    monkeypatch.setattr(fl, "OpsStore", lambda: OpsStore(tmp_path / "ops.db"))
    monkeypatch.setattr(fl.fade, "settle", lambda db, official: calls.append("fade") or ["X +1.00%"])
    monkeypatch.setattr(fl, "settle_scanner", lambda db: calls.append("scanner") or [])

    def boom(db):
        calls.append("shadow")
        raise RuntimeError("bars unavailable")
    monkeypatch.setattr(fl, "settle_long_shadow", boom)
    assert fl.cmd_settle() == 0 and calls == ["fade", "scanner", "shadow"]


def test_digest_shows_the_shadow_record(db):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from sgt.ops import digest
    fade_row(db, "ABCD", "settled")
    ls.run(db, lambda s, d: DAY, lambda *a: 0.0, now=NOW)
    text = digest.build(db, datetime(2026, 11, 9, 20, 50, tzinfo=ZoneInfo("America/New_York")), fills=[],
                        problems=[], broker=None, research=[])
    assert "(paper, no orders)" in text and "L4a 1 avg" in text


def test_halt_reason_is_what_the_official_settle_writes():
    """Contract: an alert with no bar within 5 minutes (a halt) is voided by the settle with attach_trades' status."""
    import sys
    from datetime import date, datetime, timedelta, timezone
    from pathlib import Path
    from types import SimpleNamespace
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import telegram_alert_test as tat

    t0 = datetime(2025, 3, 3, 15, 0, tzinfo=timezone.utc)
    halted = [SimpleNamespace(ts=t0 + timedelta(minutes=m), open=2.0, close=2.0) for m in range(60) if not 1 <= m < 12]
    store = SimpleNamespace(read=lambda *a: halted)
    alert = pd.DataFrame({"symbol": ["ABCD"], "alert_type": ["volume_spike"], "posted_at": [pd.Timestamp(t0 + timedelta(seconds=30))],
                          "has_news": [None], "session": [date(2025, 3, 3)]})
    assert tat.attach_trades(alert, store).iloc[0].status == ls.HALT_REASON


def test_missing_bars_are_retried_not_recorded(db):
    """Review F-1: a failed bar download must not mark a settled event as 'no start bar' forever."""
    fade_row(db, "ABCD", "settled")
    now = pd.Timestamp("2026-11-10 01:40", tz="UTC").to_pydatetime()
    assert ls.run(db, lambda s, d: DAY.iloc[0:0], lambda *a: 0.0, now=now) == [] and shadow(db) == []
    ls.run(db, lambda s, d: DAY, lambda *a: 0.0, now=now)
    assert {"L4a", "L4b"} <= {r["kind"] for r in shadow(db)}


def test_only_recent_events_are_due(db):
    """Review F-2: the first run after a deploy does not back-fetch the whole history."""
    fade_row(db, "ABCD", "settled")
    calls = []
    late = pd.Timestamp("2026-11-30 01:40", tz="UTC").to_pydatetime()       # 21 days after the event
    ls.run(db, lambda s, d: calls.append(s) or DAY, lambda *a: 0.0, now=late)
    assert calls == [] and shadow(db) == []


def test_longs_also_run_on_alerts_the_short_skipped(db):
    """Owner 2026-11-10 'longs first': F6 (news) and F7 (beta >= 3) skips are long candidates; other filters are not."""
    from sgt.fast import fade
    for sym, reason in (("NEWS", fade.NEWS_REASON), ("BETA", fade.BETA_REASON), ("WIN", "outside 09:30-15:44")):
        with db.tx() as c:
            c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (sym, "2026-11-09", "volume_spike",
                      POSTED.isoformat(), "-1", hash(sym) % 10_000, "filtered", reason, POSTED.isoformat()))
    ls.run(db, lambda s, d: DAY, lambda *a: 0.01, now=NOW)
    syms = {r["symbol"] for r in shadow(db) if r["kind"] == "L4a"}
    assert syms == {"NEWS", "BETA"}
    assert all(r["cost_rt"] == 0.01 for r in shadow(db) if r["net"] is not None)     # modelled cost (no fade cost)


def test_filtered_alert_outside_the_price_band_gets_no_long(db):
    from sgt.fast import fade
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)", ("PENNY", "2026-11-09", "volume_spike",
                  POSTED.isoformat(), "-1", 7, "filtered", fade.NEWS_REASON, POSTED.isoformat()))
    cheap = DAY.assign(open=DAY.open / 20, high=DAY.high / 20, low=DAY.low / 20, close=DAY.close / 20)
    ls.run(db, lambda s, d: cheap, lambda *a: 0.0, now=NOW)
    assert [(r["kind"], r["reason"]) for r in shadow(db)] == [("-", "no start bar")]
