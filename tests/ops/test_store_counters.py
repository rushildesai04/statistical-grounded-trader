from __future__ import annotations

import threading

from sgt.ops.store import OpsStore


def test_take_is_capped_and_atomic_across_threads(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    ok = []

    def worker():
        for _ in range(20):
            ok.append(db.take("k", 1, 50))
    ts = [threading.Thread(target=worker) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(ok) == 50 and db.flag("k") == "50"              # never over the cap, every success counted


def test_add_and_garbage_values(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    assert db.add("n", 5) == 5 and db.add("n", -2) == 3
    db.set_flag("bad", "abc")
    assert db.add("bad", 1) == 1 and db.take("bad", 1, 1) is False
