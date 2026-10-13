"""Deterministic identifiers.

``client_order_id`` must be identical across retries and replays of the same
decision so brokers (and SimBroker) can dedupe. It is derived only from the
decision's identity, never from wall-clock time or randomness.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping


def _digest(*parts: str, length: int = 24) -> str:
    h = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()
    return h[:length]


def client_order_id(*, strategy_id: str, account_id: str, symbol: str,
                    signal_ts: datetime, reason: str, leg: int = 0) -> str:
    """Stable order id for one decision. ``leg`` distinguishes split/child orders.

    Brokers cap client ids (Alpaca: 128 chars); 32 chars keeps every venue happy.
    """
    return "sgt-" + _digest(strategy_id, account_id, symbol, signal_ts.isoformat(), reason, str(leg), length=28)


def config_hash(config: Mapping[str, Any]) -> str:
    """Content hash of a JSON-serializable config (key order independent)."""
    blob = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
