"""The reconciled-ledger script's own logic (labels, flags, the price-test read), with a fake tape: no network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import exec_ledger as el  # noqa: E402
from sgt.fast import exec_model as em  # noqa: E402

T0 = pd.Timestamp("2026-11-18 15:00:00", tz="UTC")              # 10:00 ET
CLOSE = pd.Timestamp("2026-11-18 21:00:00", tz="UTC")


class FakeApi:
    """Flat $10 tape, bid 9.95 / ask 10.05, a print every 20 s; ``prior`` and ``fail`` set per test."""

    def __init__(self, prior=(10.0, 9.8, 10.1), fail=False, low=None):
        self.prior_v, self.fail, self.low = prior, fail, low

    def day(self, symbol, session):
        ts = pd.date_range(T0 - pd.Timedelta(minutes=30), periods=120, freq="1min")
        f = pd.DataFrame({"ts": ts, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "rth": True})
        if self.low is not None:
            f.loc[5, "low"] = self.low                            # 09:35 ET, long before any entry here
        f.attrs["close"] = CLOSE
        return f

    def prior(self, symbol, session):
        if self.fail:
            raise em.NoData("daily")
        return self.prior_v

    def tape(self, symbol, session):
        if self.fail:
            raise em.NoData("bars")
        tr = np.array([((T0 + pd.Timedelta(seconds=k)).value, 10.0, 100.0) for k in range(0, 4000, 20)], dtype=float)
        f = self.day(symbol, session)
        return em.Tape(lambda s, at: {"bid": 9.95, "ask": 10.05, "bid_size": 1e9, "ask_size": 1e9},
                       lambda s, a, b: tr[(tr[:, 0] >= a.value) & (tr[:, 0] < b.value)], f, CLOSE)


def short(**kw):
    r = {"id": 1, "symbol": "AAA", "session": "2026-11-18", "alert_type": "volume_spike", "weight": 0.05,
         "posted_at": "2026-11-18T15:00:20+00:00", "created_at": "2026-11-18T15:00:40+00:00",
         "est_entry_at": "2026-11-18T15:01:12+00:00", "est_entry": 10.0, "entry_ts": "2026-11-18 15:01:00+00:00",
         "entry": 10.0, "cover": 9.9, "reason": "60 min", "net": 0.0, "ib_avail": 100_000, "alp_shortable": 1}
    return {**r, **kw}


def test_labels():
    ok = {"status": "ok"}
    assert el._label(ok, []) == "MODELED" and el._label(ok, ["thin", "late", "borrow_conflict", "halt"]) == "MODELED"
    for bad in ("ssr", "no_borrow", "no_data", "unresolved"):
        assert el._label(ok, [bad]) == "UNRESOLVED"
    assert el._label({"status": "no_trade"}, []) == "NO TRADE"
    assert el._label({"status": "no_data"}, []) == "UNRESOLVED" and el._label({}, []) == "UNRESOLVED"


def test_a_placeable_short_is_modeled_with_both_figures_and_the_original_untouched():
    r = el.short_row(FakeApi(), short())
    assert r["label"] == "MODELED" and r["flags"] == [] and r["model"]["entry"] == 9.95 and r["model"]["exit"] == 10.05
    assert r["orig"] == {"entry_ts": "2026-11-18 15:01:00+00:00", "entry": 10.0, "exit": 9.9, "reason": "60 min", "net": 0.0,
                         "est_entry": 10.0}
    assert r["stress_latency_net"] == pytest.approx(r["model"]["net"])
    json.dumps(r, default=el._plain)                              # every value can be written to the ledger


@pytest.mark.parametrize("kw, flag, label", [
    ({"ib_avail": 0}, "no_borrow", "UNRESOLVED"),
    ({"ib_avail": 400}, "no_borrow", "UNRESOLVED"),               # the trade needs 500 shares ($5,000 at $10)
    ({"ib_avail": 500}, None, "MODELED"),
    ({"ib_avail": None}, "borrow_unknown", "MODELED"),            # unknown is said, and is not counted as unplaceable
    ({"alp_shortable": 0}, "borrow_conflict", "MODELED"),
    ({"created_at": "2026-11-18T15:01:05+00:00"}, "late", "MODELED"),
])
def test_short_flags(kw, flag, label):
    r = el.short_row(FakeApi(), short(**kw))
    assert r["label"] == label and (flag in r["flags"] if flag else r["flags"] == [])


def test_price_test_today_carried_over_and_the_numbers_it_was_read_from():
    today = el.short_row(FakeApi(low=8.9), short())               # 8.9 <= 0.9 x the prior close 10.0, at 09:35
    assert "ssr" in today["flags"] and today["label"] == "UNRESOLVED"
    assert today["ssr_basis"] == {"prior_close": 10.0, "low_before_entry": 8.9, "prior_low": 9.8, "close_before_prior": 10.1}
    carried = el.short_row(FakeApi(prior=(10.0, 8.0, 10.1)), short())          # yesterday fell 20% from its prior close
    assert "ssr" in carried["flags"]
    assert "ssr" not in el.short_row(FakeApi(prior=(None, None, None)), short())["flags"]     # unknown is not a flag


def test_a_failed_request_is_one_unresolved_row_never_a_number():
    r = el.short_row(FakeApi(fail=True), short())
    assert r["model"]["status"] == "no_data" and r["label"] == "UNRESOLVED" and r["stress_latency_net"] is None
    row = {"id": 2, "symbol": "AAA", "session": "2026-11-18", "kind": "L8", "rules": "B1", "weight": 0.01, "stop": 9.0,
           "created_at": "2026-11-18T15:01:12+00:00", "est_entry": 10.0, "fill_ts": "x", "entry": 10.0, "exit": 9.0,
           "exit_reason": "stop", "net": -0.1, "reason": "stop"}
    assert el.long_row(FakeApi(fail=True), row)["label"] == "UNRESOLVED"


def test_long_row_buys_the_ask_and_an_ask_under_the_stop_is_no_trade():
    row = {"id": 2, "symbol": "AAA", "session": "2026-11-18", "kind": "L8", "rules": "B1", "weight": 0.01, "stop": 9.0,
           "created_at": "2026-11-18T15:01:12+00:00", "est_entry": 10.0, "fill_ts": "x", "entry": 10.0, "exit": 9.0,
           "exit_reason": "stop", "net": -0.1, "reason": "stop"}
    r = el.long_row(FakeApi(), row)
    assert r["label"] == "MODELED" and r["model"]["entry"] == 10.05 and r["model"]["reason"] == "time"
    assert el.long_row(FakeApi(), {**row, "stop": 10.05})["label"] == "NO TRADE"


def test_settled_check_reads_the_database_read_only_and_reports_the_gap(tmp_path):
    from sgt.ops.store import OpsStore

    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        for i, status in ((1, "settled"), (2, "settled"), (3, "filtered")):
            c.execute("INSERT INTO fade_trades(id, symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "created_at) VALUES (?,?,?,?,?,?,?,?,?)", (i, f"S{i}", "2026-11-18", "volume_spike", "x", "-1", i, status, "x"))
    led = tmp_path / "ledger.jsonl"
    led.write_text(json.dumps({"book": "short", "id": 1, "session": "2026-11-18", "label": "MODELED",
                               "model": {"status": "ok", "net": 0.1}, "orig": {"net": 0.1}}) + "\n")
    r = el.settled_check(led, str(tmp_path / "ops.db"))
    assert r["short"]["expected"] == 2 and r["short"]["missing"] == [2] and r["ok"] is False


def test_a_recovered_alerts_trade_is_priced_like_any_other_and_carries_its_label():
    """Listener recovery 2026-11-22: the cohort's definition does not change; the ledger row says how the alert came."""
    live, back = el.short_row(FakeApi(), short()), el.short_row(FakeApi(), short(
        via="replay", recovered_at="2026-11-18T15:00:38+00:00"))
    assert (live["via"], live["recovered_at"]) == (None, None)
    assert (back["via"], back["recovered_at"]) == ("replay", "2026-11-18T15:00:38+00:00")
    assert back["model"] == live["model"] and back["label"] == live["label"] == "MODELED"
