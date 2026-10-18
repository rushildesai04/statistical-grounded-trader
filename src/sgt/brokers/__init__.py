"""sgt.brokers — broker adapters and the simulation cost model.

See docs/IMPLEMENTATION_PLAN.md §8, §13. Every adapter implements
``sgt.core.interfaces.BrokerAdapter`` and must pass tests/brokers/test_contract.py.
"""
from sgt.brokers.alert_only import (
    AlertOnlyBroker,
    LogNotifier,
    Notifier,
    PollResult,
    WebhookNotifier,
)
from sgt.brokers.alpaca import AlpacaBroker
from sgt.brokers.costs import DEFAULT_COST_MODEL, CostModel
from sgt.brokers.ibkr import IBKRBroker
from sgt.brokers.schwab import SchwabBroker
from sgt.brokers.schwab_auth import SchwabTokenManager
from sgt.brokers.sim import SimBroker
from sgt.brokers.snaptrade import (
    ReadOnlyPositionSource,
    SnapTradeBroker,
    account_state_from,
    positions_from_json,
)
from sgt.brokers.snaptrade_client import SnapTradeClient, compute_signature

__all__ = [
    "AlertOnlyBroker",
    "AlpacaBroker",
    "CostModel",
    "DEFAULT_COST_MODEL",
    "IBKRBroker",
    "LogNotifier",
    "Notifier",
    "PollResult",
    "ReadOnlyPositionSource",
    "SchwabBroker",
    "SchwabTokenManager",
    "SimBroker",
    "SnapTradeBroker",
    "SnapTradeClient",
    "WebhookNotifier",
    "account_state_from",
    "compute_signature",
    "positions_from_json",
]
