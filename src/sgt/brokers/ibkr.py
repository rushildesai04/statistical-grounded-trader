"""Interactive Brokers adapter (paper and live) over a local IB Gateway / TWS.

Talks to a locally running IB Gateway or Trader Workstation through the maintained
``ib_async`` library (successor of ``ib_insync``). The brokerage connection is an
OPTIONAL extra: ``pip install .[ibkr]``. ``ib_async`` is imported lazily so the base
install and the rest of the test-suite never require it; a missing package raises a
clear, actionable error only when a real connection is attempted.

Connection target comes from secrets (keychain or ``SGT_SECRET_*``), never the repo:
``ibkr_host`` (default ``127.0.0.1``), ``ibkr_port`` (default ``4002`` paper / ``4001``
live) and ``ibkr_client_id`` (default ``1``). A paper flag paired with a live port (or
vice versa) is refused at construction so a paper session can never reach the live
gateway. Every network call is bounded by ``settings.http_timeout_s`` and a lost
connection surfaces as ``BrokerUnavailableError`` rather than hanging.

Idempotency: the deterministic ``client_order_id`` is written to IBKR
``Order.orderRef``. Before placing, ``submit`` scans open trades, completed orders and
executions for a matching ``orderRef`` and returns the existing ack instead of placing
a duplicate. Fills are keyed on ``Execution.execId`` and de-duplicated across polls.

Long-only enforcement is CLIENT-SIDE: a SELL whose quantity exceeds the available long
position (current position minus shares already working in other SELL orders) is
rejected before it reaches the gateway. IBKR exposes no API flag reporting whether an
account is permissioned for shorting, so (unlike Alpaca's ``shorting_enabled`` check)
the live account's short permission cannot be verified programmatically — the
client-side guard here is the enforcement, and the account should additionally be
configured cash / no-margin so the broker itself cannot borrow to short.

[VERIFY] against the installed ``ib_async`` (pinned ``>=2.1,<3``) before live use. API
names relied on, confirmed against ib_async 2.1.0 source:
- ``IB.connectAsync(host, port, clientId, timeout=, account=, readonly=)``,
  ``IB.isConnected()``, ``IB.disconnect()``.
- ``IB.accountSummaryAsync(account) -> [AccountValue(account, tag, value, currency, ...)]``
  with tags ``TotalCashValue`` / ``SettledCash`` / ``BuyingPower`` (currency USD).
- ``IB.reqPositionsAsync() -> [Position(account, contract, position, avgCost)]``.
- ``IB.placeOrder(contract, order) -> Trade`` (synchronous; returns immediately with a
  ``Trade`` whose status updates asynchronously), ``IB.cancelOrder(order) -> Trade``.
- ``IB.openTrades() -> [Trade]``, ``IB.reqCompletedOrdersAsync(apiOnly) -> [Trade]``,
  ``IB.reqExecutionsAsync(execFilter=None) -> [Fill(contract, execution, commissionReport, time)]``.
- ``Trade.contract`` (``.symbol``), ``Trade.order`` (``.orderRef``, ``.action``,
  ``.permId``, ``.orderId``), ``Trade.orderStatus`` (``.status``, ``.filled``,
  ``.remaining``), ``Trade.log[*]`` (``.time``, ``.status``, ``.message``, ``.errorCode``).
- ``Execution`` (``.execId``, ``.orderRef``, ``.permId``, ``.orderId``, ``.side`` in
  ``{"BOT","SLD"}``, ``.shares``, ``.price``, ``.time``), ``CommissionReport.commission``.
- Factories ``Stock(symbol, "SMART", "USD")``, ``LimitOrder(action, qty, lmt, ...)``,
  ``MarketOrder(action, qty, ...)``, ``StopOrder(action, qty, stop, ...)`` accepting
  ``tif``/``outsideRth``/``orderRef``/``account`` kwargs.
- ``OrderStatus.DoneStates`` terminal set includes ``Inactive`` / ``ApiCancelled`` /
  ``Cancelled`` / ``Filled``; a rejection presents as ``Inactive`` / ``ApiCancelled``
  with a non-zero ``errorCode`` in ``Trade.log``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol

from sgt.config import get_settings
from sgt.core.interfaces import (
    AdapterHealth,
    BrokerAdapter,
    BrokerAuthError,
    BrokerRejectedError,
    BrokerUnavailableError,
)
from sgt.core.secrets import get_secret
from sgt.core.types import (
    AccountState,
    Fill,
    OrderAck,
    OrderIntent,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)

log = logging.getLogger(__name__)

# Gateway/TWS listening ports. Keeping a paper flag aligned with the port is the last
# line of defence against routing a paper session to a live account.
_PAPER_PORTS = {4002, 7497}  # IB Gateway paper, TWS paper
_LIVE_PORTS = {4001, 7496}   # IB Gateway live, TWS live

_TIF_MAP = {TimeInForce.DAY: "DAY", TimeInForce.IOC: "IOC", TimeInForce.GTC: "GTC"}
_REJECT_STATES = {"Inactive", "ApiCancelled"}

_STATUS_MAP = {
    "PendingSubmit": OrderStatus.PENDING,
    "ApiPending": OrderStatus.PENDING,
    "PreSubmitted": OrderStatus.WORKING,
    "Submitted": OrderStatus.WORKING,
    "PendingCancel": OrderStatus.WORKING,
    "ApiUpdate": OrderStatus.WORKING,
    "Filled": OrderStatus.FILLED,
    "Cancelled": OrderStatus.CANCELED,
    "ApiCancelled": OrderStatus.CANCELED,
    "Inactive": OrderStatus.REJECTED,
}


class IBClient(Protocol):
    """Minimal structural view of ``ib_async.IB`` the adapter depends on.

    A faithful fake implementing these members (plus the ``Stock``/``LimitOrder``/
    ``MarketOrder``/``StopOrder`` factories) lets the whole suite run without the
    ``ib_async`` package or a live gateway.
    """

    async def connectAsync(self, host: str, port: int, clientId: int, **kwargs: Any) -> Any: ...
    def isConnected(self) -> bool: ...
    def disconnect(self) -> None: ...
    async def accountSummaryAsync(self, account: str = "") -> list[Any]: ...
    async def reqPositionsAsync(self) -> list[Any]: ...
    def placeOrder(self, contract: Any, order: Any) -> Any: ...
    def cancelOrder(self, order: Any) -> Any: ...
    def openTrades(self) -> list[Any]: ...
    async def reqCompletedOrdersAsync(self, apiOnly: bool) -> list[Any]: ...
    async def reqExecutionsAsync(self, execFilter: Any | None = None) -> list[Any]: ...


class IBKRBroker(BrokerAdapter):
    name = "ibkr"
    supports_extended_hours = True       # LIMIT only; MARKET is refused for ext-hours
    supports_broker_side_stops = True    # native STP orders (RTH)

    def __init__(
        self,
        account_id: str,
        ib_account: str,
        *,
        paper: bool,
        ib: IBClient | None = None,
    ) -> None:
        if not ib_account:
            raise ValueError("ib_account (IBKR account number, e.g. 'DU1234567') is required")
        self.account_id = account_id
        self._ib_account = ib_account
        self.is_paper = paper
        self._paper = paper

        settings = get_settings()
        self._timeout = settings.http_timeout_s
        self._host = get_secret("ibkr_host", required=False) or "127.0.0.1"
        port_raw = get_secret("ibkr_port", required=False)
        self._port = int(port_raw) if port_raw else (4002 if paper else 4001)
        cid_raw = get_secret("ibkr_client_id", required=False)
        self._client_id = int(cid_raw) if cid_raw else 1
        self._assert_port_matches_mode()

        self._ib: IBClient | None = ib
        # ib_async exposes IB and the Stock/LimitOrder/... factories on one module. A
        # fake that provides the same factory attributes doubles as the object factory,
        # so tests never import ib_async. Real connections resolve the module lazily.
        self._factory_obj: Any = ib if (ib is not None and hasattr(ib, "Stock")) else None
        self._seen_fill_ids: set[str] = set()

    def _assert_port_matches_mode(self) -> None:
        if self._paper and self._port in _LIVE_PORTS:
            raise ValueError(
                f"paper=True but ibkr_port={self._port} is a LIVE gateway port; "
                "refusing to route a paper session to a live account")
        if not self._paper and self._port in _PAPER_PORTS:
            raise ValueError(
                f"paper=False but ibkr_port={self._port} is a PAPER gateway port; "
                "refusing to route a live session to a paper account")

    # ------------------------------------------------------------------ seam helpers
    def _factory(self) -> Any:
        if self._factory_obj is None:
            self._factory_obj = _import_ib_async()
        return self._factory_obj

    def _client(self) -> IBClient:
        if self._ib is None:
            self._ib = _import_ib_async().IB()
        return self._ib

    def _require_connected(self) -> IBClient:
        ib = self._client()
        if not ib.isConnected():
            raise BrokerUnavailableError("IB gateway connection lost; reconnect required")
        return ib

    # ------------------------------------------------------------------ adapter API
    async def connect(self) -> None:
        ib = self._client()
        try:
            await ib.connectAsync(
                self._host, self._port, self._client_id,
                timeout=self._timeout, account=self._ib_account, readonly=False,
            )
        except (ConnectionError, OSError, TimeoutError) as exc:
            raise BrokerUnavailableError(
                f"cannot reach IB gateway at {self._host}:{self._port}: {type(exc).__name__}"
            ) from exc
        if not ib.isConnected():
            raise BrokerAuthError(
                f"IB gateway at {self._host}:{self._port} did not establish a session; "
                "check that the gateway is logged in and the API is enabled")

    async def account_state(self) -> AccountState:
        ib = self._require_connected()
        values = await ib.accountSummaryAsync(self._ib_account)
        tags: dict[str, float] = {}
        for v in values:
            if getattr(v, "account", self._ib_account) not in (self._ib_account, "", "All"):
                continue
            if getattr(v, "currency", "USD") not in ("USD", "", "BASE"):
                continue
            try:
                tags[v.tag] = float(v.value)
            except (TypeError, ValueError):
                continue
        positions = await ib.reqPositionsAsync()
        pos = tuple(
            Position(symbol=p.contract.symbol, qty=int(p.position), avg_price=float(p.avgCost))
            for p in positions
            if getattr(p, "account", self._ib_account) == self._ib_account
            and getattr(p.contract, "secType", "STK") == "STK"
            and int(p.position) != 0
        )
        cash = tags.get("TotalCashValue", 0.0)
        return AccountState(
            account_id=self.account_id,
            cash=cash,
            settled_cash=tags.get("SettledCash", cash),
            buying_power=tags.get("BuyingPower", 0.0),
            positions=pos,
        )

    async def submit(self, intent: OrderIntent) -> OrderAck:
        if intent.order_type is OrderType.MARKET and intent.extended_hours:
            raise BrokerRejectedError("MARKET orders are not allowed in extended hours")
        self._require_connected()

        existing = await self._find_existing(intent.client_order_id)
        if existing is not None:
            return existing

        if intent.side is Side.SELL:
            await self._guard_long_only(intent)

        contract = self._factory().Stock(intent.symbol, "SMART", "USD")
        order = self._build_order(intent)
        trade = self._client().placeOrder(contract, order)
        self._raise_if_rejected(trade)
        return self._ack_from_trade(trade)

    async def cancel(self, client_order_id: str) -> OrderAck:
        ib = self._require_connected()
        for trade in ib.openTrades():
            if trade.order.orderRef != client_order_id:
                continue
            if _map_status(trade.orderStatus) not in (OrderStatus.CANCELED, OrderStatus.FILLED,
                                                      OrderStatus.REJECTED, OrderStatus.EXPIRED):
                ib.cancelOrder(trade.order)
            return OrderAck(
                client_order_id, _broker_id(trade.order), OrderStatus.CANCELED,
                _trade_ts(trade), "canceled",
                symbol=trade.contract.symbol, side=_side(trade.order.action), remaining_qty=0)
        return OrderAck(client_order_id, "", OrderStatus.CANCELED,
                        datetime.now(timezone.utc), "unknown order")

    async def open_orders(self) -> list[OrderAck]:
        ib = self._require_connected()
        return [self._ack_from_trade(t) for t in ib.openTrades()]

    async def fills(self) -> AsyncIterator[Fill]:
        ib = self._require_connected()
        for f in await ib.reqExecutionsAsync():
            execution = f.execution
            fill_id = str(execution.execId)
            if fill_id in self._seen_fill_ids:
                continue
            self._seen_fill_ids.add(fill_id)
            commission = getattr(getattr(f, "commissionReport", None), "commission", 0.0) or 0.0
            yield Fill(
                client_order_id=str(execution.orderRef or ""),
                broker_order_id=_broker_id(execution),
                account_id=self.account_id,
                symbol=f.contract.symbol,
                side=Side.BUY if str(execution.side).upper().startswith("B") else Side.SELL,
                qty=int(execution.shares),
                price=float(execution.price),
                ts=_to_utc(execution.time),
                fill_id=fill_id,
                fee=abs(float(commission)),
            )

    async def health(self) -> AdapterHealth:
        try:
            ib = self._client()
        except ImportError as exc:
            return AdapterHealth(ok=False, detail=str(exc))
        connected = ib.isConnected()
        return AdapterHealth(
            ok=connected,
            detail=f"connected to {self._host}:{self._port}" if connected
            else "not connected to IB gateway")

    async def close(self) -> None:
        if self._ib is not None and self._ib.isConnected():
            self._ib.disconnect()

    # ------------------------------------------------------------------ idempotency
    async def _find_existing(self, client_order_id: str) -> OrderAck | None:
        ib = self._client()
        for trade in ib.openTrades():
            if trade.order.orderRef == client_order_id:
                return self._ack_from_trade(trade)
        for trade in await ib.reqCompletedOrdersAsync(False):
            if trade.order.orderRef == client_order_id:
                return self._ack_from_trade(trade)
        for f in await ib.reqExecutionsAsync():
            if str(f.execution.orderRef or "") == client_order_id:
                return self._ack_from_execution(f)
        return None

    async def _guard_long_only(self, intent: OrderIntent) -> None:
        """Reject a SELL that would exceed the available long position (client-side).

        Available = current long position minus shares already working in OTHER open
        SELL orders for the symbol. Idempotency already returned for a re-submitted id,
        so open SELLs counted here are always distinct orders.
        """
        ib = self._client()
        held = 0
        for p in await ib.reqPositionsAsync():
            if (getattr(p, "account", self._ib_account) == self._ib_account
                    and p.contract.symbol == intent.symbol):
                held += int(p.position)
        working_sells = 0
        for trade in ib.openTrades():
            if (trade.contract.symbol == intent.symbol
                    and _side(trade.order.action) is Side.SELL):
                working_sells += int(getattr(trade.orderStatus, "remaining", 0) or 0)
        available = held - working_sells
        if intent.qty > available:
            raise BrokerRejectedError(
                f"long-only: SELL {intent.qty} {intent.symbol} exceeds available long "
                f"position {available} (held={held}, working_sells={working_sells})")

    # ------------------------------------------------------------------ mapping
    def _build_order(self, intent: OrderIntent) -> Any:
        f = self._factory()
        action = "BUY" if intent.side is Side.BUY else "SELL"
        tif = _TIF_MAP[intent.tif]
        common = dict(tif=tif, orderRef=intent.client_order_id, account=self._ib_account)
        if intent.order_type is OrderType.LIMIT:
            # Extended-hours fills require outsideRth; harmless during RTH.
            return f.LimitOrder(action, intent.qty, intent.limit_price,
                                outsideRth=intent.extended_hours, **common)
        if intent.order_type is OrderType.STOP:
            # Broker-side stops are RTH only at IBKR; never set outsideRth.
            return f.StopOrder(action, intent.qty, intent.stop_price, outsideRth=False, **common)
        return f.MarketOrder(action, intent.qty, **common)

    def _raise_if_rejected(self, trade: Any) -> None:
        status = getattr(trade.orderStatus, "status", "")
        if status not in _REJECT_STATES:
            return
        err = next((e for e in getattr(trade, "log", []) if getattr(e, "errorCode", 0)), None)
        if err is None and not getattr(trade, "advancedError", ""):
            return
        detail = getattr(err, "message", "") or getattr(trade, "advancedError", "") or status
        code = getattr(err, "errorCode", 0) if err is not None else 0
        raise BrokerRejectedError(
            f"IBKR rejected order {trade.order.orderRef!r}: {status} "
            f"(code={code}) {detail}")

    def _ack_from_trade(self, trade: Any) -> OrderAck:
        status = _map_status(trade.orderStatus)
        remaining = int(getattr(trade.orderStatus, "remaining", 0) or 0)
        return OrderAck(
            client_order_id=str(trade.order.orderRef or ""),
            broker_order_id=_broker_id(trade.order),
            status=status,
            ts=_trade_ts(trade),
            message=str(getattr(trade.orderStatus, "status", "")),
            symbol=trade.contract.symbol,
            side=_side(trade.order.action),
            remaining_qty=remaining,
        )

    def _ack_from_execution(self, f: Any) -> OrderAck:
        execution = f.execution
        return OrderAck(
            client_order_id=str(execution.orderRef or ""),
            broker_order_id=_broker_id(execution),
            status=OrderStatus.FILLED,
            ts=_to_utc(execution.time),
            message="filled",
            symbol=f.contract.symbol,
            side=Side.BUY if str(execution.side).upper().startswith("B") else Side.SELL,
            remaining_qty=0,
        )


def _import_ib_async() -> Any:
    try:
        import ib_async
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "the IBKR adapter requires the 'ib_async' package; install the optional "
            "extra with `pip install .[ibkr]`"
        ) from exc
    return ib_async


def _map_status(order_status: Any) -> OrderStatus:
    status = getattr(order_status, "status", "")
    mapped = _STATUS_MAP.get(status)
    if mapped is not None:
        return mapped
    filled = float(getattr(order_status, "filled", 0) or 0)
    remaining = float(getattr(order_status, "remaining", 0) or 0)
    if filled > 0 and remaining > 0:
        return OrderStatus.PARTIALLY_FILLED
    return OrderStatus.WORKING


def _side(action: str) -> Side:
    return Side.BUY if str(action).upper().startswith("B") else Side.SELL


def _broker_id(order_or_exec: Any) -> str:
    perm = getattr(order_or_exec, "permId", 0)
    if perm:
        return str(perm)
    return str(getattr(order_or_exec, "orderId", "") or "")


def _trade_ts(trade: Any) -> datetime:
    log_entries = getattr(trade, "log", None) or []
    if log_entries:
        return _to_utc(log_entries[-1].time)
    return datetime.now(timezone.utc)


def _to_utc(ts: Any) -> datetime:
    if not isinstance(ts, datetime):
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)
