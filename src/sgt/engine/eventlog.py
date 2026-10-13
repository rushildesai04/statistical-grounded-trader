"""Append-only event sinks (plan §7.3, §8 determinism).

Every order, fill, risk verdict, candidate and transition is emitted here with a
monotonically increasing ``seq`` and the bar-derived ``ts`` (NEVER wall-clock), so
two runs over identical inputs produce byte-identical logs. ``JsonlEventSink``
fsyncs on durable kinds (orders/fills) so a crash cannot lose an execution record;
``InMemoryEventSink`` keeps events in a list for tests and in-process inspection.
"""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from sgt.core.interfaces import EventSink

_DURABLE_KINDS = frozenset({"order", "fill", "risk_reject"})


class InMemoryEventSink(EventSink):
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._seq = 0

    def emit(self, kind: str, ts: datetime, payload: Mapping[str, Any]) -> None:
        self.events.append({"seq": self._seq, "kind": kind, "ts": ts, "payload": dict(payload)})
        self._seq += 1

    def flush(self) -> None:  # nothing buffered
        return None


class JsonlEventSink(EventSink):
    """One JSON object per line, append-only. Deterministic given deterministic
    inputs: ``seq`` is a counter and ``ts`` comes from the caller (bar time)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")
        self._seq = 0

    def emit(self, kind: str, ts: datetime, payload: Mapping[str, Any]) -> None:
        record = {"seq": self._seq, "kind": kind, "ts": ts.isoformat(), "payload": dict(payload)}
        self._fh.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        self._seq += 1
        if kind in _DURABLE_KINDS:
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()

    def __enter__(self) -> "JsonlEventSink":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
