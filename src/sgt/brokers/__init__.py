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
from sgt.brokers.sim import SimBroker

__all__ = [
    "AlertOnlyBroker",
    "AlpacaBroker",
    "CostModel",
    "DEFAULT_COST_MODEL",
    "LogNotifier",
    "Notifier",
    "PollResult",
    "SimBroker",
    "WebhookNotifier",
]
