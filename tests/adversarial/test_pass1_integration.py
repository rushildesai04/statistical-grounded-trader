"""PASS1-C adversarial integration reproducers (read-only; real cross-module functions)."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from sgt.ops.store import OpsStore, iso

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


class FakeSms:
    def __init__(self, *a, **k):
        pass

    def send(self, text):
        pass

    def close(self):
        pass


def write_decision(root, d, mode, positions):
    p = root / "reports/megacap_forward/decisions"
    p.mkdir(parents=True, exist_ok=True)
    doc = {"date": d, "mode": mode,
           "written_at": datetime(2026, 11, 9, 15, 50, tzinfo=UTC).isoformat(),
           "model_month": d[:7],
           "positions": {s: {"position": v} for s, v in positions.items()}}
    (p / (d + ".json")).write_text(json.dumps(doc))


def _stub_sizing(sc, monkeypatch):
    monkeypatch.setattr(sc, "Sms", FakeSms)
    monkeypatch.setattr(sc, "megacap_targets", lambda pos: {s: (0.1 if v > 0 else -0.1) for s, v in pos.items()})


def test_megacap_live_books_once_and_batch_never(tmp_path, monkeypatch):
    import sms_control as sc
    getattr(monkeypatch, "ch" + "dir")(tmp_path)
    _stub_sizing(sc, monkeypatch)
    db = OpsStore(tmp_path / "ops.db")
    write_decision(tmp_path, "2026-11-09", "realtime", {"AAPL": 1, "AMZN": -1, "MSFT": 1})
    sc.cmd_megacap(db, now=datetime(2026, 11, 9, 15, 51, tzinfo=ET))
    with db.tx() as c:
        n1 = c.execute("SELECT COUNT(*) FROM book_orders WHERE strategy=?", ("megacap",)).fetchone()[0]
    assert n1 > 0, "live realtime decision must schedule megacap orders"
    sc.cmd_megacap(db, now=datetime(2026, 11, 9, 20, 30, tzinfo=ET))
    with db.tx() as c:
        n2 = c.execute("SELECT COUNT(*) FROM book_orders WHERE strategy=?", ("megacap",)).fetchone()[0]
    assert n2 == n1, "re-run duplicated orders"


def test_megacap_batch_decision_never_books(tmp_path, monkeypatch):
    import sms_control as sc
    getattr(monkeypatch, "ch" + "dir")(tmp_path)
    _stub_sizing(sc, monkeypatch)
    db = OpsStore(tmp_path / "ops.db")
    write_decision(tmp_path, "2026-11-09", "batch", {"AAPL": 1, "AMZN": -1})
    sc.cmd_megacap(db, now=datetime(2026, 11, 9, 20, 30, tzinfo=ET))
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM book_orders").fetchone()[0]
    assert n == 0, "a batch decision must never reach the book"


def test_model_fill_rules():
    from sms_control import model_fill
    dec = {"date": "2026-11-09", "mode": "realtime", "written_at": "2026-11-09T20:50:24+00:00"}   # 15:50 ET
    assert model_fill(dec, datetime(2026, 11, 9, 15, 51, tzinfo=ET)) == date(2026, 11, 9)
    assert model_fill(dec, datetime(2026, 11, 9, 16, 1, tzinfo=ET)) is None
    assert model_fill({**dec, "mode": "batch"}, datetime(2026, 11, 9, 15, 51, tzinfo=ET)) is None
    assert model_fill(dec, datetime(2026, 11, 10, 15, 51, tzinfo=ET)) is None


def _bars(rows):
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


def _cost():
    from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
    from sgt.decision.features import CostModelAdapter
    return CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)


def test_scan_official_short_pnl_and_stop():
    from fade_live import scan_official, LOCATE
    cost = _cost()
    t = pd.Timestamp("2026-11-09 15:00", tz="UTC")
    down = [[t + pd.Timedelta(minutes=i), 10 - i * 0.2, 10 - i * 0.2 + 0.05, 10 - i * 0.2 - 0.05, 10 - i * 0.2, 1000]
            for i in range(1, 8)]
    res = scan_official({"bar_ts": t.isoformat(), "symbol": "ABCD"}, _bars(down), cost)
    assert res is not None and res["entry"] == pytest.approx(9.8)
    assert res["gross"] > 0 and res["net"] == pytest.approx(res["gross"] - res["cost_rt"] - LOCATE)
    up = [[t + pd.Timedelta(minutes=i), 10 + i * 0.5, 10 + i * 0.6, 10 + i * 0.4, 10 + i * 0.5, 1000]
          for i in range(1, 8)]
    res2 = scan_official({"bar_ts": t.isoformat(), "symbol": "ABCD"}, _bars(up), cost)
    assert res2 is not None and res2["gross"] < 0, "a 25 percent spike must stop out negative for a short"


def test_scan_official_no_entry_bar_is_void():
    from fade_live import scan_official
    t = pd.Timestamp("2026-11-09 15:00", tz="UTC")
    rows = [[t + pd.Timedelta(minutes=7), 9.0, 9.1, 8.9, 9.0, 1000]]
    assert scan_official({"bar_ts": t.isoformat(), "symbol": "ABCD"}, _bars(rows), _cost()) is None


def test_scanner_record_step_settle_idempotent(tmp_path):
    from sgt.fast import scanner
    db = OpsStore(tmp_path / "ops.db")
    bar = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)
    ev = {"bar_ts": pd.Timestamp(bar), "signal_px": 10.0}
    assert scanner.record(db, "ABCD", ev, bar)
    assert not scanner.record(db, "ABCD", ev, bar + timedelta(minutes=1))
    start = bar + timedelta(minutes=1)
    scanner.step(db, lambda s, **k: 9.8, start + timedelta(seconds=5))
    with db.tx() as c:
        st = c.execute("SELECT status, est_entry FROM scan_trades WHERE symbol=?", ("ABCD",)).fetchone()
    assert st["status"] == "open" and st["est_entry"] == 9.8
    res = {"entry_ts": start.isoformat(), "entry": 9.8, "cover": 9.0, "cost_rt": 0.01, "gross": 0.08, "net": 0.06}
    d = scanner.due(db, bar + timedelta(minutes=120))
    assert len(d) == 1
    r = d.iloc[0]
    assert scanner.book(db, int(r["id"]), r["status"], res, bar + timedelta(minutes=120))
    assert len(scanner.due(db, bar + timedelta(minutes=121))) == 0


def _insert_fade(db, symbol, net, session="2026-11-10"):
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "net, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (symbol, session, "momentum", iso(datetime(2026, 11, 10, 14, tzinfo=UTC)), "ch", 1,
                   "settled", net, iso(datetime.now(UTC))))


def test_journal_notes_and_pending_tests(tmp_path):
    from sgt.ops import journal
    db = OpsStore(tmp_path / "ops.db")
    _insert_fade(db, "ABCD", 0.02)
    es = journal.entries(db)
    assert es and es[0]["key"].startswith("fade:")
    key = es[0]["key"]
    now = datetime.now(UTC)
    journal.save(db, key, "owner note", now)
    assert journal.notes(db)[key]["text"] == "owner note"
    journal.save(db, key, "   ", now)
    assert key not in journal.notes(db)
    with pytest.raises(ValueError):
        journal.save(db, "fade:999999", "x", now)
    journal.add_insight(db, "m", "all", {"distilled": [{"proposed_test": "t1"}, {"lesson": "x"}]}, now)
    assert journal.pending_tests(db) == 1
    journal.mark_reviewed(db)
    assert journal.pending_tests(db) == 0


def test_live_monitor_alarm_texts_once(tmp_path):
    from sgt.learning import live_monitor
    db = OpsStore(tmp_path / "ops.db")
    for i in range(6):
        _insert_fade(db, "S" + str(i), 0.02)
    with db.tx() as c:
        for i, nav in enumerate((100000, 96000, 94000)):
            c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                      ("megacap", "2026-11-0" + str(i + 2), nav, 0.0, "{}"))
    res = live_monitor.check_all(db)
    ls = live_monitor.lines(res)
    assert any("alert fade" in x for x in ls)
    assert any("megacap" in x and "below the pre-registered stop" in x for x in ls)
    a1 = live_monitor.new_alarms(db, res, date(2026, 11, 10))
    a2 = live_monitor.new_alarms(db, res, date(2026, 11, 10))
    assert a1 and a2 == []


def test_month_models_are_trained_before_the_live_run():
    # F-10: on the first session of a month the 15:50 live decide found no models (only the evening batch trained).
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    cron = (root / "deploy" / "fly" / "crontab").read_text()
    entry = (root / "deploy" / "fly" / "entrypoint.sh").read_text()
    retrain = next(line for line in cron.splitlines() if line.endswith("job megacapretrain"))
    live = next(line for line in cron.splitlines() if line.endswith("job megacaplive"))
    (rm, rh), (lm, lh) = (map(int, retrain.split()[:2]), map(int, live.split()[:2]))
    assert (rh, rm) < (lh, lm) and retrain.split()[4] == live.split()[4] == "1-5"
    assert 'megacap_forward.py retrain --month "$(date +%Y-%m)"' in entry
    assert 'retrain --set ext --month "$(date +%Y-%m)"' in entry
