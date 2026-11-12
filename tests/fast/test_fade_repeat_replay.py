"""F8 end to end on the real Nov 4-12 live alert stream (2026-11-12 validation): every alert replayed in order through
fade.on_alert on a fresh DB, with each entered trade given the status it had live. The F8 skips must be exactly the
research rule's repeats (fade_repeat_days_study.mark_repeats) on the same events."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from sgt.fast import fade
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import fade_repeat_days_study as rd  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/live_alerts_2026-11-04_12.json"


def test_f8_on_the_live_alert_stream_matches_the_research_rule(tmp_path):
    live = pd.DataFrame(json.loads(FIXTURE.read_text())).sort_values(["session", "id"])
    db = OpsStore(tmp_path / "ops.db")
    out = []
    for r in live.itertuples():
        posted = datetime.fromisoformat(r.posted_at)
        now = posted + timedelta(seconds=5)
        ap.arm(db, now)
        res = fade.on_alert(db, fade.LiveAlert(r.symbol, r.alert_type, posted, "-1", int(r.id), None), now=now)
        if res.startswith("pending"):                      # what happened next live, so later days see it
            st = r.status if r.status in ("settled", "est_closed", "skipped", "void") else "settled"
            with db.tx() as c:
                c.execute("UPDATE fade_trades SET status=?, reason=? WHERE symbol=? AND session=? AND status='pending'",
                          (st, r.reason, r.symbol, r.session))
        out.append((r.symbol, r.session, res))
    o = pd.DataFrame(out, columns=["symbol", "session", "res"])
    f8 = set(map(tuple, o[o.res == "filtered: repeat"][["symbol", "session"]].values))
    ev = o[~o.res.str.startswith(("not a cell", "filtered: window"))].drop_duplicates(["symbol", "session"])
    ev = rd.mark_repeats(ev.assign(net=0.0, news_own=0, split="dev"))
    research = set(map(tuple, ev[ev.repeat][["symbol", "session"]].values))
    assert f8 == research == {("STAK", "2026-11-06"), ("OMH", "2026-11-09"), ("STAK", "2026-11-10"),
                              ("OMH", "2026-11-10"), ("PEPG", "2026-11-11"), ("RETO", "2026-11-12")}
