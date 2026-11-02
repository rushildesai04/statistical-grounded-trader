"""Holdout vault: code-enforced held-out discipline (2026-11-02, trader_ideas_design §4).

Each study registers its training and held-out date ranges in ``config/holdout_registry.json``.
A ``Vault`` starts in TRAIN mode: reading held-out dates raises ``HoldoutViolation``, and
``train_rows`` drops them explicitly. ``unlock`` switches to SCORE mode only when the study's
pre-registration and the scorer script are committed and unmodified in git, and only once per
study (a second unlock needs a written ``rescore_reason``). Every unlock is written to the trial
ledger (run id ``vault:<study>``) with the commit hash, so "was the rule frozen before the
held-out data was read?" is answered by the ledger, not by memory.

Scope: guards reads that go through the vault. A script that bypasses it (direct HTTP, raw
parquet reads) is not policed; scorers must read held-out data only via ``Vault.read`` / ``frame``.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

REGISTRY = Path("config/holdout_registry.json")


class HoldoutViolation(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Study:
    id: str
    train: tuple[date, date]
    holdout: tuple[date, date]
    prereg: str


def load_registry(path: Path = REGISTRY) -> dict[str, Study]:
    raw = json.loads(path.read_text())
    out = {}
    for sid, s in raw["studies"].items():
        tr = tuple(date.fromisoformat(x) for x in s["train"])
        ho = tuple(date.fromisoformat(x) for x in s["holdout"])
        if not (tr[0] <= tr[1] < ho[0] <= ho[1]):
            raise ValueError(f"study {sid}: train must end before holdout starts")
        out[sid] = Study(sid, tr, ho, s["prereg"])
    return out


class UnlockLedger(Protocol):
    def unlocks(self, study_id: str) -> int: ...

    def record(self, study_id: str, info: dict) -> None: ...


class TrialLedgerUnlocks:
    """Unlock records in the shared trial ledger (sgt.decision.trial_ledger)."""

    def unlocks(self, study_id: str) -> int:
        from sgt.db.session import get_session, init_db
        from sgt.decision.trial_ledger import trial_count

        init_db()
        with get_session() as s:
            return trial_count(s, f"vault:{study_id}")

    def record(self, study_id: str, info: dict) -> None:
        from sgt.db.session import get_session, init_db
        from sgt.decision.trial_ledger import record_trial

        init_db()
        with get_session() as s:
            record_trial(s, run_id=f"vault:{study_id}", kind="vault-unlock", params=info, metrics={})


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise HoldoutViolation(f"git {' '.join(args)} failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()


def committed_clean(repo: Path, path: str) -> str:
    """Last commit touching ``path``; raises unless it is tracked and has no uncommitted change."""
    _git(repo, "ls-files", "--error-unmatch", path)
    if _git(repo, "status", "--porcelain", "--", path):
        raise HoldoutViolation(f"{path} has uncommitted changes; commit it before unlocking")
    return _git(repo, "log", "-1", "--format=%H", "--", path)


class Vault:
    def __init__(self, study_id: str, *, registry: Path = REGISTRY, repo: Path = Path("."),
                 ledger: UnlockLedger | None = None) -> None:
        studies = load_registry(repo / registry if not registry.is_absolute() else registry)
        if study_id not in studies:
            raise KeyError(f"study {study_id!r} not in {registry}")
        self.study = studies[study_id]
        self.repo = repo
        self.ledger = ledger or TrialLedgerUnlocks()
        self.mode = "train"
        self.commit: str | None = None

    @property
    def holdout_start(self) -> date:
        return self.study.holdout[0]

    def check_range(self, start: date, end: date) -> None:
        if self.mode == "train" and end >= self.holdout_start:
            raise HoldoutViolation(f"{self.study.id}: {start}..{end} reaches held-out data from "
                                   f"{self.holdout_start}; locked until unlock()")
        if end > self.study.holdout[1]:
            raise HoldoutViolation(f"{self.study.id}: {end} is past the registered holdout end")

    def train_rows(self, df: pd.DataFrame, date_col: str | None = None) -> pd.DataFrame:
        """Rows strictly before the held-out start (index or ``date_col``); allowed in any mode."""
        d = pd.to_datetime(df[date_col] if date_col else df.index)
        return df[np.asarray(d < pd.Timestamp(self.holdout_start))]

    def frame(self, df: pd.DataFrame, date_col: str | None = None) -> pd.DataFrame:
        """Pass ``df`` through the guard: in TRAIN mode any held-out row raises."""
        d = pd.to_datetime(df[date_col] if date_col else df.index)
        if len(d):
            self.check_range(d.min().date(), d.max().date())
        return df

    def read(self, store, symbol: str, start: date, end: date) -> pd.DataFrame:
        """Guarded ``ParquetBarStore.read_frame``."""
        self.check_range(start, end)
        return store.read_frame(symbol, start, end)

    def unlock(self, scorer: str, rescore_reason: str | None = None) -> str:
        prior = self.ledger.unlocks(self.study.id)
        if prior and not rescore_reason:
            raise HoldoutViolation(f"{self.study.id} was already unlocked {prior}x; scoring is one-shot "
                                   "(pass rescore_reason to record a deliberate re-score)")
        commits = {p: committed_clean(self.repo, p) for p in (self.study.prereg, scorer)}
        head = _git(self.repo, "rev-parse", "HEAD")
        info = {"study": self.study.id, "head": head, "prereg_commit": commits[self.study.prereg],
                "scorer": scorer, "scorer_commit": commits[scorer], "attempt": prior + 1,
                "rescore_reason": rescore_reason, "at": datetime.now(timezone.utc).isoformat()}
        self.ledger.record(self.study.id, info)
        self.mode, self.commit = "score", head
        return head
