"""Event sinks: monotonic seq, bar-time (not wall-clock) timestamps, and the
durable JSONL form being append-only and replay-stable."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sgt.engine.eventlog import InMemoryEventSink, JsonlEventSink


def test_in_memory_sink_assigns_monotonic_seq():
    sink = InMemoryEventSink()
    ts = datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)
    for i in range(5):
        sink.emit("fill", ts, {"i": i})
    assert [e["seq"] for e in sink.events] == [0, 1, 2, 3, 4]
    assert all(e["ts"] == ts for e in sink.events)


def test_jsonl_sink_is_append_only_sorted_and_bar_timed(tmp_path):
    p = tmp_path / "events.jsonl"
    ts = datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)
    sink = JsonlEventSink(p)
    sink.emit("order", ts, {"b": 2, "a": 1})
    sink.emit("fill", ts, {"z": 9})
    sink.close()
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["seq"] == 0 and rec["kind"] == "order" and rec["ts"] == ts.isoformat()
    # sort_keys makes the serialization canonical (a before b).
    assert lines[0].index('"a"') < lines[0].index('"b"')
    # reopening appends rather than truncating
    sink2 = JsonlEventSink(p)
    sink2.emit("candidate", ts, {})
    sink2.close()
    assert len(p.read_text().splitlines()) == 3
