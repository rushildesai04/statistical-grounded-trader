"""Holdout vault (src/sgt/research/vault.py): reads locked until a committed prereg + scorer unlock."""
from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from sgt.research.vault import HoldoutViolation, Vault


class MemLedger:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def unlocks(self, study_id: str) -> int:
        return sum(r["study"] == study_id for r in self.rows)

    def record(self, study_id: str, info: dict) -> None:
        self.rows.append(info)


def git(repo: Path, *a: str) -> None:
    subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@example.com")
    git(tmp_path, "config", "user.name", "t")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/holdout_registry.json").write_text(json.dumps({"studies": {"s1": {
        "train": ["2016-01-04", "2022-12-30"], "holdout": ["2023-01-03", "2026-10-30"],
        "prereg": "prereg.md"}}}))
    (tmp_path / "prereg.md").write_text("rule")
    (tmp_path / "score.py").write_text("print(1)")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


class FakeStore:
    def read_frame(self, symbol, start, end):
        return pd.DataFrame({"x": [1]}, index=[pd.Timestamp(start)])


def test_train_mode_blocks_holdout_reads(repo: Path):
    v = Vault("s1", repo=repo, ledger=MemLedger())
    v.read(FakeStore(), "AAA", date(2016, 1, 4), date(2022, 12, 30))
    with pytest.raises(HoldoutViolation):
        v.read(FakeStore(), "AAA", date(2022, 6, 1), date(2023, 1, 3))
    df = pd.DataFrame({"v": [1, 2]}, index=pd.to_datetime(["2022-12-30", "2023-01-03"]))
    assert len(v.train_rows(df)) == 1
    with pytest.raises(HoldoutViolation):
        v.frame(df)


def test_unlock_requires_committed_clean_files_and_is_one_shot(repo: Path):
    led = MemLedger()
    v = Vault("s1", repo=repo, ledger=led)
    (repo / "prereg.md").write_text("rule edited after the fact")
    with pytest.raises(HoldoutViolation, match="uncommitted"):
        v.unlock("score.py")
    git(repo, "commit", "-qam", "edit")
    (repo / "untracked.py").write_text("x")
    with pytest.raises(HoldoutViolation):
        v.unlock("untracked.py")
    head = v.unlock("score.py")
    assert v.mode == "score" and len(head) == 40
    assert led.rows[0]["prereg_commit"] and led.rows[0]["attempt"] == 1
    v.read(FakeStore(), "AAA", date(2023, 1, 3), date(2026, 10, 30))
    with pytest.raises(HoldoutViolation, match="past the registered"):
        v.read(FakeStore(), "AAA", date(2023, 1, 3), date(2026, 11, 2))
    v2 = Vault("s1", repo=repo, ledger=led)
    with pytest.raises(HoldoutViolation, match="one-shot"):
        v2.unlock("score.py")
    v2.unlock("score.py", rescore_reason="data vendor fix, documented in prereg amendment")
    assert led.rows[1]["attempt"] == 2


def test_registry_rejects_overlap(repo: Path):
    (repo / "config/holdout_registry.json").write_text(json.dumps({"studies": {"bad": {
        "train": ["2016-01-04", "2023-06-30"], "holdout": ["2023-01-03", "2026-10-30"], "prereg": "p"}}}))
    with pytest.raises(ValueError):
        Vault("bad", repo=repo, ledger=MemLedger())
