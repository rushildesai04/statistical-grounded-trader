from __future__ import annotations

import json
from datetime import date

from sgt.ops import weekly
from sgt.ops.store import OpsStore


def test_weekly_review_text(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        for sym, sess, typ, net, own in (("RAYA", "2026-11-06", "news", -0.2175, 1), ("STAK", "2026-11-06",
                                          "volume_increasing", -0.0498, 1), ("CRIS", "2026-11-04", "volume_spike",
                                          0.2564, 0), ("OLD", "2026-10-30", "news", 0.5, 0)):
            c.execute("INSERT INTO fade_trades (symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "reason, created_at, net, news_own) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (sym, sess, typ, f"{sess}T15:00:00+00:00", "-1", hash(sym) % 1000, "settled", "x",
                       f"{sess}T15:00:00+00:00", net, own))
    (tmp_path / "learning").mkdir()
    (tmp_path / "learning" / "shadow_summary.json").write_text(json.dumps({"line": "learning: 38/300 forward trades"}))
    text = weekly.build(db, date(2026, 11, 6), reports=tmp_path)
    assert text.startswith("[SGT] Week of Nov 2: weekly review")
    assert "Fade: 3 trades, 1 won" in text and "news trades 2" in text and "no-news 1 avg +25.6%" in text
    assert "best CRIS +25.6%, worst RAYA -21.8%" in text and "Learning: 38/300" in text
    assert "OLD" not in text                                      # last week's trade is excluded


def test_empty_week():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        text = weekly.build(OpsStore(Path(d) / "ops.db"), date(2026, 11, 6), reports=Path(d))
    assert "no settled trades this week" in text
