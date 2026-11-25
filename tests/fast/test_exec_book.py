"""The reconciled ledger's reader (execution model v1): the forward sample, the nightly line, the registered review."""
from __future__ import annotations

import json

import pytest

from sgt.fast import exec_book as eb


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def row(book, i, net, orig=None, label="MODELED", stress=None, session="2026-11-23"):
    return {"book": book, "id": i, "session": session, "label": label, "orig": {"net": orig},
            "model": {"status": "ok", "net": net, "stress_extreme_net": stress}}


MIXED = [0.12] * 10 + [-0.04] * 10 + [-0.044] * 10      # three days: one good, two bad; average +1.2%


def many(book, nets, start=0, per_day=3):
    """Trades in order, ``per_day`` a session, from 2026-11-23 on."""
    return [row(book, start + i, x, session=f"2026-12-{1 + i // per_day:02d}") for i, x in enumerate(nets)]


def test_no_file_an_empty_file_or_a_damaged_file_is_no_line(tmp_path):
    p = tmp_path / "ledger.jsonl"
    assert eb.rows(p) == [] and eb.line(p) is None
    p.write_text("not json\n{\"book\": \"short\"}\n[1, 2]\n")
    assert eb.rows(p) == [] and eb.line(p) is None


def test_summary_keeps_modeled_apart_from_flagged_and_the_last_line_per_trade_wins(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, [row("short", 1, 0.10, 0.12), row("short", 2, -0.20, -0.21, stress=-0.30),
              row("short", 3, 0.50, 0.33, label="UNRESOLVED"), row("short", 1, 0.08, 0.12),      # id 1 written again
              row("long", 7, -0.05, -0.04, stress=-0.09), {"book": "other", "id": 9}])
    s = eb.summary(p)
    assert s["short"]["trades"] == 3 and s["short"]["modeled"] == 2 and s["short"]["unresolved"] == 1
    assert s["short"]["model"] == pytest.approx((0.08 - 0.20) / 2)          # the flagged +50% is NOT in the average
    assert s["short"]["original"] == pytest.approx((0.12 - 0.21) / 2)
    assert s["short"]["stress"] == pytest.approx((0.08 - 0.30) / 2)         # a trade with no stop keeps its modeled net
    assert s["long"]["modeled"] == 1 and s["long"]["se"] is None


def test_trades_before_the_forward_sample_never_count_for_the_line_or_the_review(tmp_path):
    p = tmp_path / "ledger.jsonl"
    old = [row("short", i, 0.30, session="2026-11-20") for i in range(60)]            # the re-priced history
    write(p, old)
    assert eb.line(p) is None and eb.review("short", p) == {"state": "collecting", "n": 0, "mean": None, "lower": None,
                                                              "sessions": 0}
    write(p, old + [row("short", 100, -0.10, -0.09), row("short", 101, 0.20, 0.1, "UNRESOLVED")])
    t = eb.line(p)
    # 2026-11-24: the line names the last session it covers (the ledger is written after the summary that shows it)
    assert t.startswith("executable baseline since 11-23, trades through 11-23 (a model, not fills; a research ledger, "
                        "not stop protection): ")
    write(p, old + [row("short", 100, -0.10, -0.09), row("long", 7, 0.01, session="2026-11-25")])
    assert eb.line(p).startswith("executable baseline since 11-23, trades through 11-25 (")
    assert "shorts 1 of 50 with no exclusion flag: -10.0% a trade at real quotes (original record -9.0%)" in t
    assert "1 flagged (no listed shares or price test) and not counted; review: collecting" in t


def test_non_numbers_are_never_averaged(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, [row("short", 1, 0.10, None), row("short", 2, "x", 0.1), row("short", 3, True, 0.1)])
    s = eb.summary(p)["short"]
    assert s["modeled"] == 1 and s["model"] == pytest.approx(0.10) and s["original"] is None
    assert eb.review("short", p)["n"] == 1


# ---- the registered review (exec_baseline_2026-11-21.md, section 6) ----
def test_long_retires_at_30_when_the_average_is_at_or_under_zero_and_only_the_first_30_count(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, many("long", [-0.02] * 29))
    assert eb.review("long", p)["state"] == "collecting"
    write(p, many("long", [-0.02] * 30 + [0.50] * 10))                     # later winners cannot rescue the sample
    v = eb.review("long", p)
    assert v["state"] == "RETIRE" and v["n"] == 30 and v["mean"] == pytest.approx(-0.02)
    write(p, many("long", [0.01, -0.01] * 15))                             # exactly zero: retire
    assert eb.review("long", p)["state"] == "RETIRE"


def test_keep_needs_a_positive_average_and_a_lower_bound_above_the_floor(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, many("long", [0.02, 0.03, 0.025] * 10))                       # steady: bound far above -1%
    v = eb.review("long", p)
    assert v["state"].startswith("KEEP") and v["lower"] > 0.02 and v["sessions"] == 10
    write(p, many("long", MIXED, per_day=10))                              # +1.2% average carried by ONE good day of three
    v = eb.review("long", p)
    assert v["mean"] > 0 and v["lower"] < -0.01 and v["state"].startswith("INCONCLUSIVE")   # without that day: negative


def test_the_lower_bound_resamples_days_not_trades(tmp_path):
    # the same 30 results: spread over 30 days the bound is tight; packed into 2 days it is wide
    nets = [0.05, -0.03] * 15
    assert eb._bound([f"d{i}" for i in range(30)], nets) > eb._bound(["a"] * 15 + ["b"] * 15, sorted(nets))
    assert eb._bound(["a"] * 30, nets) is None                             # one session: no bound, so never "keep"
    p = tmp_path / "ledger.jsonl"
    write(p, many("long", [0.02] * 30, per_day=30))
    assert eb.review("long", p)["state"].startswith("INCONCLUSIVE")


def test_an_inconclusive_review_is_extended_once_and_then_final(tmp_path):
    p = tmp_path / "ledger.jsonl"
    mixed = MIXED
    write(p, many("long", mixed + [0.0] * 29, per_day=10))                 # 59 trades: still extended
    assert eb.review("long", p)["state"].startswith("INCONCLUSIVE")
    write(p, many("long", mixed + [0.0] * 30, per_day=10))                 # 60: final
    v = eb.review("long", p)
    assert v["n"] == 60 and v["state"].startswith("NOT ESTABLISHED")
    write(p, many("long", mixed + [-0.05] * 30, per_day=10))
    assert eb.review("long", p)["state"] == "RETIRE"
    write(p, many("long", mixed + [0.0] * 5, per_day=10))                  # the date ends the extension too
    assert eb.review("long", p, today="2027-02-26")["state"].startswith("INCONCLUSIVE")
    assert eb.review("long", p, today="2027-02-27")["state"].startswith("NOT ESTABLISHED")


def test_the_final_date_judges_a_book_that_never_reached_its_count(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, many("short", [-0.03] * 12))
    assert eb.review("short", p, today="2027-02-27")["state"] == "RETIRE"
    write(p, many("short", [-0.03] * 9))
    assert eb.review("short", p, today="2027-02-27")["state"].startswith("NOT ESTABLISHED: too few")
    assert eb.review("short", p, today="2026-12-31")["state"] == "collecting"


def test_short_thresholds_and_the_one_early_look(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, many("short", [-0.02] * 50))
    assert eb.review("short", p)["state"] == "RETIRE"                      # at -2%
    write(p, many("short", [-0.01] * 50))
    assert eb.review("short", p)["state"].startswith("INCONCLUSIVE")       # between -2% and 0: neither
    write(p, many("short", [0.03, 0.04] * 25))
    assert eb.review("short", p)["state"].startswith("KEEP")
    write(p, many("short", [-0.06] * 25))
    assert eb.review("short", p)["state"].startswith("EARLY LOOK MET")
    write(p, many("short", [-0.06] * 24))
    assert eb.review("short", p)["state"] == "collecting"                  # not before the 25th trade
    write(p, many("short", [-0.04] * 25 + [-0.30] * 5))
    assert eb.review("short", p)["state"] == "collecting"                  # the look reads the FIRST 25 only, once
    write(p, many("long", [-0.04] * 15))
    assert eb.review("long", p)["state"].startswith("EARLY LOOK MET")


def test_flagged_trades_never_enter_the_review(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, many("short", [-0.03] * 50) + [row("short", 900 + i, 0.40, label="UNRESOLVED", session="2026-12-01") for i in range(40)])
    v = eb.review("short", p)
    assert v["state"] == "RETIRE" and v["n"] == 50 and v["mean"] == pytest.approx(-0.03)


# ---- reconciliation of an import (audit 2026-11-22) ----
def test_reconcile_names_what_is_missing_extra_unpriced_and_superseded(tmp_path):
    p = tmp_path / "ledger.jsonl"
    assert eb.reconcile({"short": {1, 2}, "long": set()}, p) == {
        "short": {"expected": 2, "held": 0, "missing": [1, 2], "extra": [], "no_data": []},
        "long": {"expected": 0, "held": 0, "missing": [], "extra": [], "no_data": []}, "superseded_lines": 0, "ok": False}
    bad = {**row("short", 2, None), "model": {"status": "no_data"}}
    write(p, [row("short", 1, 0.1), bad, row("short", 2, -0.1), row("short", 9, 0.0), row("long", 5, 0.0)])
    r = eb.reconcile({"short": {1, 2, 3}, "long": {5}}, p)
    assert r["short"] == {"expected": 3, "held": 3, "missing": [3], "extra": [9], "no_data": []}       # 2 was retried: priced
    assert r["long"]["held"] == 1 and r["superseded_lines"] == 1 and r["ok"] is False
    write(p, [row("short", 1, 0.1), row("short", 1, 0.1), row("short", 2, 0.2), bad, row("long", 5, 0.0)])
    r = eb.reconcile({"short": {1, 2}, "long": {5}}, p)
    assert r["ok"] is True and r["superseded_lines"] == 2 and r["short"]["no_data"] == [2]             # last line wins: unpriced


def test_importing_twice_changes_nothing_the_reader_sees(tmp_path):
    p = tmp_path / "ledger.jsonl"
    once = [row("short", 1, 0.1), row("short", 2, -0.2), row("long", 5, 0.03)]
    write(p, once)
    before = (eb.summary(p), eb.reconcile({"short": {1, 2}, "long": {5}}, p)["ok"])
    write(p, once + once)                                                   # the same import appended again
    assert (eb.summary(p), eb.reconcile({"short": {1, 2}, "long": {5}}, p)["ok"]) == before


def test_the_line_labels_thin_quotes_and_delay_sensitive_trades_without_touching_the_average(tmp_path):
    """2026-11-24: JZ's cover met a quote smaller than the order ("thin"), and GLXG read -20.5% with the 1 s delay
    and +6.0% with the 5 s one. Both are labels on the counted trades; the average, the review and the sample are
    what they were, and neither delay is picked."""
    p = tmp_path / "ledger.jsonl"
    plain = [row("short", 1, -0.034, -0.065), row("short", 2, 0.218, 0.246), row("short", 3, -0.205, -0.212)]
    write(p, plain)
    before, was = eb.line(p), eb.review("short", p)
    assert "smaller than the order" not in before and "change" not in before
    write(p, [plain[0], {**plain[1], "flags": ["thin", "borrow_conflict"], "stress_latency_net": 0.234},
              {**plain[2], "stress_latency_net": 0.0596}, row("short", 4, 0.3, 0.3, "UNRESOLVED") | {"flags": ["thin", "ssr"],
                                                                                                  "stress_latency_net": -0.1}])
    assert eb.sensitivity(p, eb.REVIEW_FROM) == {"short": {"thin": 1, "flips": 1}, "long": {"thin": 0, "flips": 0}}
    t = eb.line(p)
    assert ("shorts 3 of 50 with no exclusion flag: -0.7% a trade at real quotes (original record -1.0%), -0.7% with "
            "stops at the worst price of their minute; 1 filled against a quote smaller than the order (assumed filled "
            "in full at that quote); 1 changes sign with a 5 s delay in place of 1 s (both kept as sensitivity, neither "
            "chosen); 1 flagged") in t
    assert eb.review("short", p) == was and "+6.0%" not in t and "5.96" not in t      # no favourable figure is shown


def test_an_incomplete_ledger_says_so(tmp_path):
    p = tmp_path / "ledger.jsonl"
    write(p, [row("short", 1, 0.1)])
    assert eb.behind({"short": {1}, "long": set()}, p) == ""
    write(p, [row("short", 1, 0.1), {**row("long", 7, None), "model": {"status": "no_data"}, "label": "UNRESOLVED"}])
    assert eb.behind({"short": {1, 2, 3}, "long": {7}}, p) == ("; ⚠️ ledger incomplete: 2 settled trades of earlier "
                                                              "sessions not in the ledger yet, 1 written without market "
                                                              "data, to be priced again")
    assert eb.behind({"short": {5}, "long": set()}, tmp_path / "none.jsonl").startswith("; ⚠️ ledger incomplete: 1 settled trade of")


def test_the_booked_reference_of_a_long_is_set_beside_the_official_offer_and_enters_no_average(tmp_path):
    p = tmp_path / "ledger.jsonl"
    q = lambda gap, age, covers: {"feed": "official", "ask_vs_booked": gap, "age_s": age, "covers_order": covers}  # noqa: E731
    rows = [{**row("long", 1, -0.02, session="2026-11-25"), "entry_quote": q(0.016, 0.4, True)},
            {**row("long", 2, 0.03, session="2026-11-25"), "entry_quote": q(-0.002, 2.0, False)},
            {**row("long", 3, 0.01, session="2026-11-27"), "entry_quote": q(0.010, 1.0, True)},
            {**row("long", 4, 0.01, session="2026-11-27"), "entry_quote": {"note": "no official offer at the booking"}},
            {**row("long", 5, 0.01, session="2026-11-27")},                                    # written before the field
            {**row("long", 6, 0.01, session="2026-11-24"), "entry_quote": q(0.5, 1.0, True)},  # before GAP_FROM
            {**row("short", 7, 0.01, session="2026-11-25"), "entry_quote": q(0.5, 1.0, True)}, # not a long
            {**row("long", 8, 0.01, session="2026-11-25"), "entry_quote": q("0.4", 1.0, True)}]  # a string is no number
    write(p, rows)
    g = eb.reference_gap(p)
    assert (g["n"], g["thin"], g["unread"]) == (3, 1, 3) and g["median"] == 0.010 and g["age"] == 1.0
    assert g["mean"] == pytest.approx((0.016 - 0.002 + 0.010) / 3)
    assert eb.reference_gap_line(p) == (
        "long entries since 11-25, booked reference against the official offer at the booking (a quote, not a fill): "
        "3 longs, the offer averaged +0.8% from the booked price (median +1.0%), quote age median 1.0 s; 1 of 3 offered "
        "fewer shares than the order; 3 with no quote read")
    before = (eb.summary(p, since=eb.REVIEW_FROM), eb.review("long", p, "2026-11-27"), eb.line(p, "2026-11-27"))
    write(p, [{k: v for k, v in r.items() if k != "entry_quote"} for r in rows])
    assert (eb.summary(p, since=eb.REVIEW_FROM), eb.review("long", p, "2026-11-27"), eb.line(p, "2026-11-27")) == before
    assert eb.reference_gap_line(p) is None and eb.reference_gap_line(tmp_path / "none.jsonl") is None
    write(p, rows[:1])
    assert eb.reference_gap_line(p) == (
        "long entries since 11-25, booked reference against the official offer at the booking (a quote, not a fill): "
        "1 long, the offer was +1.6% from the booked price, quote age 0.4 s; 0 of 1 offered fewer shares than the order")
