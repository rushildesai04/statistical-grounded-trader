"""Account registry: upsert, enable/disable, validation."""
from __future__ import annotations

import pytest

from sgt.accounts.registry import (
    AccountSpec,
    all_accounts,
    enabled_accounts,
    set_enabled,
    upsert_account,
)
from sgt.db.session import get_session


def test_account_spec_validation():
    with pytest.raises(ValueError):
        AccountSpec("A", "sim", "cash", True, 0.0, 0.0)  # weight must be > 0
    with pytest.raises(ValueError):
        AccountSpec("A", "sim", "bogus", True, 1.0, 0.0)  # bad kind
    with pytest.raises(ValueError):
        AccountSpec("", "sim", "cash", True, 1.0, 0.0)  # empty id


def test_upsert_and_load_enabled():
    with get_session() as s:
        upsert_account(s, account_id="A", broker="alpaca", kind="margin",
                       allocation_weight=2.0, max_notional=1000.0)
        upsert_account(s, account_id="B", broker="sim", enabled=False)
    with get_session() as s:
        enabled = enabled_accounts(s)
        assert [a.account_id for a in enabled] == ["A"]
        assert enabled[0].allocation_weight == 2.0
        assert len(all_accounts(s)) == 2


def test_upsert_updates_existing():
    with get_session() as s:
        upsert_account(s, account_id="A", broker="sim", allocation_weight=1.0)
    with get_session() as s:
        upsert_account(s, account_id="A", broker="sim", allocation_weight=5.0)
    with get_session() as s:
        assert enabled_accounts(s)[0].allocation_weight == 5.0


def test_set_enabled_toggles():
    with get_session() as s:
        upsert_account(s, account_id="A", broker="sim")
    with get_session() as s:
        set_enabled(s, "A", False)
    with get_session() as s:
        assert enabled_accounts(s) == []
    with get_session() as s:
        with pytest.raises(KeyError):
            set_enabled(s, "missing", True)
