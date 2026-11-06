from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import learning_report as lr  # noqa: E402


def write(root: Path, d: str, t: pd.DataFrame) -> None:
    (root / d).mkdir(parents=True)
    t.to_csv(root / d / "shadow.csv", index=False)


def test_scores_column_and_sizing_challengers_and_marks_decision_status(tmp_path):
    n = 320
    sess = [f"2026-11-{1 + i // 10:02d}" for i in range(n)]
    write(tmp_path, "fade_exits", pd.DataFrame({"session": sess, "net_champion": [0.01] * n,
                                                "net_X-EMA": [0.03] * n, "net_X-TGT": [-0.01] * n}))
    write(tmp_path, "fade_tod", pd.DataFrame({"session": sess[:40], "net": [0.02] * 40, "w": [1.5] * 40}))
    rows = {r["challenger"]: r for r in lr.build(tmp_path)}
    assert rows["X-EMA"]["status"].startswith("READY: beats") and rows["X-EMA"]["diff"] > 0
    assert rows["X-TGT"]["status"].startswith("READY: worse")
    assert rows["tod sizing"]["status"] == "collecting 40/300" and rows["tod sizing"]["chal"] == 0.03
    line = lr.summary_line(list(rows.values()))
    assert line.startswith("learning: 320/300") and "READY to promote: X-EMA" in line
    md = lr.markdown(list(rows.values()), datetime(2026, 11, 6, tzinfo=timezone.utc))
    assert md.count("\n| ") == 4 and "prereg_fade_exits_2026-11-05" in md


def test_missing_or_empty_shadows_are_skipped(tmp_path):
    assert lr.build(tmp_path) == [] and lr.summary_line([]) is None
    write(tmp_path, "fade_brain", pd.DataFrame({"session": [], "net": [], "w": []}))
    assert lr.build(tmp_path) == []
