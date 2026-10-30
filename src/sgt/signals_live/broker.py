"""Minimal Alpaca PAPER client for the signals account (fail closed on anything else).

Uses its own keychain entries (``alpaca_signals_key_id`` / ``alpaca_signals_secret_key``)
so signal trades never touch the 60/40 paper account. Every call is bounded by a
timeout; order submits are idempotent through deterministic client order ids.
"""
from __future__ import annotations

from typing import Any

import httpx

from sgt.config import get_settings
from sgt.core.secrets import get_secret

KEY_ID = "alpaca_signals_key_id"
SECRET = "alpaca_signals_secret_key"
TIMEOUT_S = 10.0


class BrokerError(RuntimeError):
    pass


class PaperBroker:
    def __init__(self, client: httpx.AsyncClient | None = None, data_client: httpx.AsyncClient | None = None,
                 key_names: tuple[str, str] = (KEY_ID, SECRET)):
        s = get_settings()
        if "paper-api" not in s.alpaca_paper_url:
            raise BrokerError(f"refusing non-paper trading URL {s.alpaca_paper_url!r}")
        headers = {"APCA-API-KEY-ID": get_secret(key_names[0]) or "",
                   "APCA-API-SECRET-KEY": get_secret(key_names[1]) or ""}
        self._t = client or httpx.AsyncClient(base_url=s.alpaca_paper_url, headers=headers, timeout=TIMEOUT_S)
        self._d = data_client or httpx.AsyncClient(base_url=s.alpaca_data_url, headers=headers, timeout=TIMEOUT_S)

    async def aclose(self) -> None:
        await self._t.aclose()
        await self._d.aclose()

    async def _call(self, c: httpx.AsyncClient, method: str, path: str, **kw: Any) -> Any:
        try:
            r = await c.request(method, path, **kw)
        except httpx.HTTPError as exc:
            raise BrokerError(f"{method} {path}: {type(exc).__name__}") from exc
        if r.status_code >= 400:
            raise BrokerError(f"{method} {path}: HTTP {r.status_code} {r.text[:200]}")
        return r.json() if r.content else None

    # ---------------------------------------------------------------- reads
    async def account(self) -> dict:
        return await self._call(self._t, "GET", "/v2/account")

    async def positions(self) -> dict[str, dict]:
        return {p["symbol"]: p for p in await self._call(self._t, "GET", "/v2/positions")}

    async def order(self, client_order_id: str) -> dict | None:
        try:
            return await self._call(self._t, "GET", "/v2/orders:by_client_order_id",
                                    params={"client_order_id": client_order_id, "nested": "true"})
        except BrokerError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    async def last_price(self, symbol: str) -> float | None:
        j = await self._call(self._d, "GET", "/v2/stocks/trades/latest", params={"symbols": symbol, "feed": "iex"})
        t = (j or {}).get("trades", {}).get(symbol)
        return float(t["p"]) if t and float(t.get("p", 0)) > 0 else None

    # ---------------------------------------------------------------- writes
    async def _submit(self, payload: dict) -> dict:
        existing = await self.order(payload["client_order_id"])
        if existing is not None:
            return existing
        return await self._call(self._t, "POST", "/v2/orders", json=payload)

    async def market(self, symbol: str, qty: int, side: str, cid: str) -> dict:
        return await self._submit({"symbol": symbol, "qty": str(qty), "side": side, "type": "market",
                                   "time_in_force": "day", "client_order_id": cid})

    async def bracket_buy(self, symbol: str, qty: int, limit: float, take_profit: float, stop: float,
                          cid: str) -> dict:
        return await self._submit({
            "symbol": symbol, "qty": str(qty), "side": "buy", "type": "limit", "limit_price": str(limit),
            "time_in_force": "day", "order_class": "bracket", "client_order_id": cid,
            "take_profit": {"limit_price": str(take_profit)}, "stop_loss": {"stop_price": str(stop)},
        })

    async def cancel(self, order_id: str) -> None:
        try:
            await self._call(self._t, "DELETE", f"/v2/orders/{order_id}")
        except BrokerError as exc:
            if "HTTP 404" not in str(exc) and "HTTP 422" not in str(exc):
                raise

    async def flatten_all(self) -> None:
        """Cancel every open order and market-close every position in the signals account."""
        await self._call(self._t, "DELETE", "/v2/positions", params={"cancel_orders": "true"})


class ObserveBroker(PaperBroker):
    """Observe mode: real prices (read-only data via the main paper keys), no orders ever.
    Lets the listener run and journal decisions before the signals account exists."""

    def __init__(self, **kw):
        super().__init__(key_names=("alpaca_key_id", "alpaca_secret_key"), **kw)
        self.would: list[dict] = []

    async def account(self) -> dict:
        return {"equity": "0", "last_equity": "0"}

    async def positions(self) -> dict[str, dict]:
        return {}

    async def order(self, client_order_id: str) -> dict | None:
        return None

    async def _submit(self, payload: dict) -> dict:
        self.would.append(payload)
        return {"id": "observe", "status": "observe", **payload}

    async def cancel(self, order_id: str) -> None:
        return None

    async def flatten_all(self) -> None:
        return None
