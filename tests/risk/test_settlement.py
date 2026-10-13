"""Settled-cash / T+1 tracker tests, including settlement across a weekend."""
from __future__ import annotations

from datetime import datetime, timezone

from sgt.core.types import Fill, Side
from sgt.risk.settlement import SettlementTracker

NOW = datetime(2026, 10, 9, 19, 30, tzinfo=timezone.utc)  # a Friday


def _fill(account, symbol, side, qty, price, fid):
    return Fill(client_order_id="c", broker_order_id="b", account_id=account, symbol=symbol,
                side=side, qty=qty, price=price, ts=NOW, fill_id=fid, fee=0.0)


def test_buy_consumes_settled_cash():
    t = SettlementTracker()
    t.register("A", 1000.0)
    t.start_session()  # Friday session
    t.record_fill(_fill("A", "WFF", Side.BUY, 100, 5.0, "f1"))
    assert t.available_settled_cash("A") == 500.0


def test_t_plus_one_settles_across_a_weekend():
    t = SettlementTracker()
    t.register("A", 1000.0)
    t.start_session()  # Friday
    t.record_fill(_fill("A", "WFF", Side.BUY, 100, 5.0, "f1"))   # settled -> 500
    t.record_fill(_fill("A", "WFF", Side.SELL, 100, 6.0, "f2"))  # +600 proceeds, pending
    assert t.available_settled_cash("A") == 500.0
    assert t.pending_settlement("A") == 600.0
    # Saturday and Sunday are NOT trading sessions: no start_session call for them.
    # Monday is the next session -> Friday's sale proceeds settle (T+1 in sessions).
    t.start_session()  # Monday
    assert t.available_settled_cash("A") == 1100.0
    assert t.pending_settlement("A") == 0.0


def test_margin_account_is_exempt():
    t = SettlementTracker()
    assert t.is_cash_account("MARGIN") is False
    assert t.available_settled_cash("MARGIN") == 0.0
    # recording a fill for an unregistered account is a no-op (no constraint)
    t.record_fill(_fill("MARGIN", "WFF", Side.BUY, 100, 5.0, "f1"))
    assert t.is_cash_account("MARGIN") is False


def test_good_faith_violation_detected_and_cleared():
    t = SettlementTracker()
    t.register("A", 100.0)  # only $100 settled
    t.start_session()
    # buying $500 with only $100 settled funds the rest from unsettled cash
    t.record_fill(_fill("A", "WFF", Side.BUY, 100, 5.0, "f1"))
    assert t.would_violate_good_faith("A", "WFF", 100) is True
    # once the next session settles the funds, the GFV exposure clears
    t.start_session()
    assert t.would_violate_good_faith("A", "WFF", 100) is False
