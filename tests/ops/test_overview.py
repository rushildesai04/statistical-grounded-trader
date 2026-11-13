"""Overview tab data (owner 2026-11-13): honest book summary, model calls with their tested record, never raises."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from sgt.fast import fade, long_book
from sgt.ops import charts, overview as ov
from sgt.ops.store import OpsStore

NOW = datetime(2026, 11, 13, 19, 40, tzinfo=timezone.utc)
STATS = {"AAA": {"long": {"n": 200, "win": 0.54, "avg": 0.003}, "short": {"n": 150, "win": 0.47, "avg": -0.001}}}
MARKS = ("date,symbol,position,ret,void,net\n"
         "2026-11-10,AAA,1,0.02,False,0.0196\n2026-11-10,BBB,0,-0.04,False,0.0\n"
         "2026-11-11,AAA,1,-0.01,False,-0.0104\n2026-11-11,BBB,0,0.01,False,0.0\n"
         "2026-11-12,AAA,-1,0.5,True,-0.5004\n")


@pytest.fixture()
def env(tmp_path):
    db = OpsStore(tmp_path / "ops" / "ops.db")
    d = tmp_path / "megacap_forward"
    (d / "decisions").mkdir(parents=True)
    (d / "marks.csv").write_text(MARKS)
    (d / "decisions" / "2026-11-11.json").write_text(json.dumps({"date": "2026-11-11", "positions": {"AAA": {"position": 0}}}))
    (d / "decisions" / "2026-11-12.json").write_text(json.dumps(
        {"date": "2026-11-12", "written_at": "2026-11-13T01:50:00+00:00",
         "positions": {"AAA": {"position": 1, "pred": 0.004}, "BBB": {"position": 0, "pred": 0.0}, "CCC": {"position": -1}}}))
    with db.tx() as c:
        for ses, nav, ret, h in (("2026-11-11", 101000, 0.01, "{}"), ("2026-11-12", 102010, 0.01, '{"AAA": 0.25}')):
            c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                      ("megacap", ses, nav, ret, h))
    return db, tmp_path


def test_books_show_state_result_and_holding(env):
    db, root = env
    db.set_flag(fade.OFF_FLAG, "1")
    b = {x["name"]: x for x in ov.books(db, root)}
    m = b["Megacap model (6 names)"]
    assert m["state"] == "on" and m["sessions"] == 2 and m["total"] == pytest.approx(0.0201) and m["holdings"] == {"AAA": 0.25}
    assert m["holding"] == pytest.approx((1 - 0.01) * (1 + 0.0) - 1)          # equal weight, compounded, void row dropped
    assert b["Alert fade (shorts)"]["state"] == "off" and b["Alert long book"]["state"] == "on"
    db.set_flag(long_book.OFF_FLAG, "1")
    assert {x["name"]: x["state"] for x in ov.books(db, root)}["Alert long book"] == "off"


def test_calls_are_the_latest_decision_with_tested_and_forward_records(env):
    _, root = env
    (c,) = ov.calls(root, STATS)
    assert (c["session"], c["names"], c["flat"]) == ("2026-11-12", 3, 1)
    a, cc = c["calls"]
    assert (a["symbol"], a["side"], a["tested_n"], a["tested_win"], a["tested_avg"]) == ("AAA", "long", 200, 0.54, 0.003)
    assert (a["forward_n"], a["forward_wins"]) == (2, 1) and a["forward_avg"] == pytest.approx((0.0196 - 0.0104) / 2)
    assert (cc["symbol"], cc["side"], cc["tested_n"], cc["forward_n"], cc["forward_avg"]) == ("CCC", "short", None, 0, None)


def test_broken_inputs_never_raise_and_are_marked(env):
    db, root = env
    (root / "megacap_forward" / "decisions" / "2026-11-13.json").write_text("{not json")
    assert ov.calls(root, STATS) == [{"set": "Megacap model (6 names)", "error": "latest decision unreadable"}]
    (root / "megacap_forward" / "marks.csv").write_text("date,symbol\nx,y\n")
    assert {x["name"]: x for x in ov.books(db, root)}["Megacap model (6 names)"]["holding"] is None
    out = ov.build(db, NOW, learning=[{"challenger": "A", "n": 5, "lower": -0.2, "status": "collecting 5/300"},
                                      {"challenger": "B", "n": 9, "lower": 0.01, "chal": float("nan")}, "junk", {"n": 1}])
    assert [r["name"] for r in out["leads"]] == ["B", "A"] and out["leads"][0]["challenger"] is None
    assert out["fade_record"] is None and out["asof"].endswith("ET")
    json.dumps(out, allow_nan=False)                                        # the page payload refuses NaN


def test_a_failing_section_is_none_not_an_exception(env, monkeypatch):
    db, _ = env
    monkeypatch.setattr(ov, "books", lambda *a: 1 / 0)
    out = ov.build(db, NOW)
    assert out["books"] is None and isinstance(out["calls"], list)


def test_fade_record_and_page_payload(env):
    db, root = env
    p = root / "fade_challengers" / "shadow.csv"
    p.parent.mkdir()
    p.write_text("symbol,session,net_champion\n" + "".join(f"S{i},2026-11-0{1 + i % 9},{-0.05 if i % 5 else 0.04}\n" for i in range(60)))
    r = ov.fade_record(db)
    assert r["n"] == 60 and r["win"] == pytest.approx(0.2) and r["alarm"] is True and r["tested_win"] == 0.573
    html = charts.render([], NOW, overview=ov.build(db, NOW))
    assert '"overview": {"asof"' in html and "Megacap model (6 names)" in html


def test_call_stats_file_covers_every_model_name():
    stats = json.loads(ov.CALL_STATS.read_text())
    assert len(stats) == 21 and all(stats[s][k]["n"] >= 20 and 0.3 < stats[s][k]["win"] < 0.7 for s in stats for k in ("long", "short"))
