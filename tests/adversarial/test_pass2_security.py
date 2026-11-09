from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from sgt.ops.store import OpsStore

CAP = 100


def _hammer(path: str, n: int, q):
    db = OpsStore(Path(path))
    got = 0
    for _ in range(n):
        if db.take("budget", 1, CAP):
            got += 1
    q.put(got)


def test_take_atomic_across_processes(tmp_path):
    path = str(tmp_path / "ops.db")
    OpsStore(Path(path))
    q = mp.Queue()
    procs = [mp.Process(target=_hammer, args=(path, 60, q)) for _ in range(6)]
    [p.start() for p in procs]
    totals = [q.get() for _ in procs]
    [p.join() for p in procs]
    db = OpsStore(Path(path))
    assert sum(totals) == CAP, f"granted {sum(totals)} but cap={CAP}"
    assert db.flag("budget") == str(CAP), f"counter={db.flag('budget')}"


def test_take_rejects_garbage_counter(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    db.set_flag("g", "not-a-number")
    assert db.take("g", 5, 10) is True
    assert db.flag("g") == "5"


def test_take_negative_amount_can_underflow_cap(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    assert db.take("n", -5, 0) is True
    assert db.flag("n") == "-5"


def test_budget_reserve_release_roundtrip(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    assert db.take("ai", 1000, 400_000) is True
    db.add("ai", -1000)
    assert int(db.flag("ai")) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
