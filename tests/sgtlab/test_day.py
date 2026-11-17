"""The lab's daily list from the ops database, and the runner's --site build (owner 2026-11-17, decision 5)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

from sgt.lab import day
from sgt.ops.store import OpsStore

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import lab  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def alert(db, sym, session, hm, status, reason=None, kind="volume_spike", mid=[0], net=None):
    mid[0] += 1
    at = pd.Timestamp(f"{session} {hm}", tz="America/New_York").tz_convert("UTC").isoformat()
    with db.tx() as c:
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                         " reason, weight, net, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (sym, session, kind, at, "-1", mid[0], status, reason, 0.0125, net, at)).lastrowid


def long_row(db, fid, sym, session, status, net=None):
    with db.tx() as c:
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, net, created_at) VALUES (?,?,?,?,?,?,?)",
                  (fid, sym, session, "L4a", status, net, "2026-11-17T15:00:00+00:00"))


def test_specs_group_a_stocks_alerts_and_put_traded_stocks_first(db):
    from sgt.fast import fade

    assert day.specs(db) == []
    alert(db, "OLD", "2026-11-13", "10:00", "settled", net=0.05)                  # third-latest session: left out
    alert(db, "YES", "2026-11-16", "11:00", "filtered", fade.NEWS_REASON)
    a = alert(db, "GP", "2026-11-17", "09:30", "filtered", fade.REPEAT_REASON, kind="intraday_high")
    alert(db, "GP", "2026-11-17", "09:34", "filtered", fade.REPEAT_REASON, kind="intraday_high")
    long_row(db, a, "GP", "2026-11-17", "est_closed")
    alert(db, "MPU", "2026-11-17", "10:02", "settled", "60 min", kind="gaining_momentum", net=-0.218)
    alert(db, "SMX", "2026-11-17", "09:59", "filtered", fade.NEWS_REASON, kind="resumed")
    b = alert(db, "RAIN", "2026-11-17", "11:40", "filtered", fade.NEWS_REASON)
    long_row(db, b, "RAIN", "2026-11-17", "none")
    got = day.specs(db)
    assert [(g["symbol"], g["session"]) for g in got] == [("MPU", "2026-11-17"), ("GP", "2026-11-17"),
                                                         ("RAIN", "2026-11-17"), ("SMX", "2026-11-17"),
                                                         ("YES", "2026-11-16")]
    gp = got[1]
    assert [x["type"] for x in gp["alerts"]] == ["intraday_high", "intraday_high"] and gp["alerts"][0]["at"] < gp["alerts"][1]["at"]
    assert "alerted in the last 7 days" in gp["live"] and "long taken (closed, official tonight)" in gp["live"]
    assert got[0]["live"] == "shorted -21.8%"
    assert "no long setup filled" in got[2]["live"]                               # watched, nothing to take
    assert "company news" in got[3]["live"]
    assert len(day.specs(db, limit=2)) == 2 and all(len(g["live"]) <= 300 for g in got)


def test_site_specs_drop_rows_that_are_not_a_ticker(db):
    alert(db, "MPU", "2026-11-17", "10:02", "settled", net=-0.2)
    alert(db, "bad sym!", "2026-11-17", "10:05", "filtered", "x")
    got = lab.site_specs(db)
    assert [s["symbol"] for s in got] == ["MPU"] and got[0]["alerts"][0]["at"].endswith("+00:00")


def test_site_build_writes_the_page_and_an_empty_day_is_not_a_failure(tmp_path, monkeypatch, capsys):
    out = tmp_path / "lab.html"
    monkeypatch.setattr(lab, "site_specs", lambda db=None: [])
    assert lab.main(["--site", "--out", str(out)]) == 0
    assert "const DATA = {" in out.read_text() and '"replays": []' in out.read_text()
    assert "0 replay(s)" in capsys.readouterr().out
    with pytest.raises(SystemExit):                                               # without --site: still an error
        lab.main(["--out", str(out)])
