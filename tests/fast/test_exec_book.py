"""The reconciled ledger's reader (execution model v1): the nightly line and the per-book summary."""
from __future__ import annotations

import json

import pytest

from sgt.fast import exec_book as eb


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def row(book, i, net, orig, label="MODELED", stress=None):
    return {"book": book, "id": i, "label": label, "orig": {"net": orig},
            "model": {"status": "ok", "net": net, "stress_extreme_net": stress}}


def test_no_file_an_empty_file_or_a_damaged_file_is_no_line(tmp_path):
    p = tmp_path / "ledger.jsonl"
    assert eb.rows(p) == [] and eb.line(p) is None
    p.write_text("not json\n{\"book\": \"short\"}\n[1, 2]\n")
    assert eb.rows(p) == [] and eb.line(p) is None


def test_summary_keeps_modeled_apart_from_unresolved_and_the_last_line_per_trade_wins(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, [row("short", 1, 0.10, 0.12), row("short", 2, -0.20, -0.21, stress=-0.30),
              row("short", 3, 0.50, 0.33, label="UNRESOLVED"), row("short", 1, 0.08, 0.12),      # id 1 written again
              row("long", 7, -0.05, -0.04, stress=-0.09), {"book": "other", "id": 9}])
    s = eb.summary(p)
    assert s["short"]["trades"] == 3 and s["short"]["modeled"] == 2 and s["short"]["unresolved"] == 1
    assert s["short"]["model"] == pytest.approx((0.08 - 0.20) / 2)          # the unresolved +50% is NOT in the average
    assert s["short"]["original"] == pytest.approx((0.12 - 0.21) / 2)
    assert s["short"]["stress"] == pytest.approx((0.08 - 0.30) / 2)         # a trade with no stop keeps its modeled net
    assert s["long"]["modeled"] == 1 and s["long"]["se"] is None


def test_line_names_the_model_the_original_the_stress_and_the_review_point(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, [row("short", 1, 0.10, 0.12), row("short", 2, -0.20, -0.21, stress=-0.30), row("short", 3, 0.5, 0.3, "UNRESOLVED")])
    t = eb.line(p)
    assert t.startswith("executable baseline (a model, not fills): shorts 2 of 100: -5.0% a trade at real quotes")
    assert "(original record -4.5%)" in t and "-10.0% with stops at the worst price of their minute" in t
    assert "1 could not be priced or placed" in t and "longs" not in t
    write(p, [row("long", i, -0.01, -0.01) for i in range(60)])
    assert "longs 60 REVIEW DUE" in eb.line(p)


def test_non_numbers_are_never_averaged(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, [row("short", 1, 0.10, None), row("short", 2, "x", 0.1), row("short", 3, True, 0.1)])
    s = eb.summary(p)["short"]
    assert s["modeled"] == 1 and s["model"] == pytest.approx(0.10) and s["original"] is None
