"""Zerodha Kite Connect broker adapter — implements the BrokerAdapter protocol.

This adapter places real orders when used with a live, authenticated Kite
client. It is only ever reached through OrderManager, which gates every BUY
through RiskEngine first. This module does not perform login/authentication
(see agentic_investing.auth) and does not decide *whether* to trade — it only
executes what OrderManager has already approved and sized.

Restart safety: Kite's order API has no client-supplied idempotency key
(only a 20-char "tag"). Before ever calling place_order, this adapter records
an "attempting" entry in the OrderStore and derives a tag from
client_order_id. If the process restarts mid-request, get_order() first
checks the store, then falls back to scanning the broker's order book by tag
to discover whether the order actually reached the exchange — it never
blindly re-submits.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from .models import Fill, Order, OrderRequest, OrderSide, OrderStatus, Position
from .order_store import OrderStore, derive_tag

# Fixed Kite Connect API constants (see https://kite.trade/docs/connect/v3/orders/).
# These never vary per client instance, so they are module-level rather than
# read off the client object (which would require every client, including
# test fakes, to declare them as mutable class attributes).
_VARIETY_REGULAR = "regular"
_PRODUCT_CNC = "CNC"
_ORDER_TYPE_MARKET = "MARKET"
_VALIDITY_DAY = "DAY"

# Kite order-book status -> our canonical OrderStatus.
# "OPEN", "TRIGGER PENDING", and other interim RMS/exchange states are all
# treated as SUBMITTED (non-terminal) until COMPLETE/REJECTED/CANCELLED.
_STATUS_MAP: dict[str, OrderStatus] = {
    "COMPLETE": OrderStatus.FILLED,
    "REJECTED": OrderStatus.REJECTED,
    "CANCELLED": OrderStatus.CANCELLED,
}


class KiteOrderClient(Protocol):
    """Subset of the Kite client used for order placement and reconciliation."""

    def place_order(self, **kwargs: Any) -> str: ...

    def cancel_order(self, variety: str, order_id: str) -> str: ...

    def orders(self) -> list[dict[str, Any]]: ...

    def order_history(self, order_id: str) -> list[dict[str, Any]]: ...

    def positions(self) -> dict[str, Any]: ...

    def margins(self, segment: str | None = None) -> dict[str, Any]: ...


class KiteBrokerAdapter:
    """Places and reconciles real Zerodha orders. No login logic lives here."""

    def __init__(self, client: KiteOrderClient, order_store: OrderStore) -> None:
        self._client = client
        self._store = order_store
        self._orders: dict[str, Order] = {}

    def place_order(self, request: OrderRequest) -> Order:
        """Idempotently place a CNC MARKET order at Zerodha.

        If ``client_order_id`` was already attempted, this checks the local
        store and the broker's order book (by tag) before deciding whether a
        genuinely new submission is safe, rather than trusting in-memory
        state alone (which would be lost on a restart).
        """

        cached = self._orders.get(request.client_order_id)
        if cached is not None:
            return cached

        existing_entry = self._store.get(request.client_order_id)
        if existing_entry is not None:
            recovered = self._recover_from_broker(request, existing_entry.tag)
            if recovered is not None:
                self._orders[request.client_order_id] = recovered
                return recovered
            # No trace at the broker despite a prior "attempting" record —
            # safe to proceed as a fresh submission using the same tag.

        entry = self._store.begin_attempt(request.client_order_id)
        broker_order_id = self._client.place_order(
            variety=_VARIETY_REGULAR,
            exchange=request.exchange,
            tradingsymbol=request.instrument,
            transaction_type=request.side.value,
            quantity=request.quantity,
            product=_PRODUCT_CNC,
            order_type=_ORDER_TYPE_MARKET,
            validity=_VALIDITY_DAY,
            tag=entry.tag,
        )
        self._store.mark_submitted(request.client_order_id, broker_order_id)
        order = self._build_order(request, broker_order_id)
        self._orders[request.client_order_id] = order
        return order

    def cancel_order(self, client_order_id: str) -> Order:
        order = self._orders.get(client_order_id)
        if order is None:
            raise KeyError(f"unknown client_order_id: {client_order_id}")
        if order.status.is_terminal:
            raise ValueError(f"cannot cancel a terminal order (status={order.status})")
        if order.broker_order_id is None:
            raise ValueError("order has no broker_order_id to cancel")
        self._client.cancel_order(_VARIETY_REGULAR, order.broker_order_id)
        order.status = OrderStatus.CANCELLED
        self._store.mark_terminal(client_order_id)
        return order

    def get_order(self, client_order_id: str) -> Order | None:
        return self._orders.get(client_order_id)

    def refresh_order(self, client_order_id: str) -> Order | None:
        """Re-fetch order history from Kite and update local terminal state."""

        order = self._orders.get(client_order_id)
        if order is None or order.broker_order_id is None:
            return order
        history = self._client.order_history(order.broker_order_id)
        if not history:
            return order
        latest = history[-1]
        self._apply_broker_state(order, latest)
        if order.status.is_terminal:
            self._store.mark_terminal(client_order_id)
        return order

    def list_orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())

    def list_positions(self) -> tuple[Position, ...]:
        payload = self._client.positions()
        positions = []
        for row in payload.get("net", []):
            quantity = int(row.get("quantity", 0))
            if quantity == 0:
                continue
            positions.append(
                Position(
                    instrument=str(row["tradingsymbol"]),
                    exchange=str(row["exchange"]),
                    quantity=quantity,
                    average_price=Decimal(str(row.get("average_price", 0))),
                )
            )
        return tuple(positions)

    def cash_balance(self) -> Decimal:
        margins = self._client.margins(segment="equity")
        available = margins.get("available", {})
        return Decimal(str(available.get("live_balance", available.get("cash", 0))))

    def _recover_from_broker(self, request: OrderRequest, tag: str) -> Order | None:
        """Scan the broker's order book by tag to recover state after a restart."""

        for row in self._client.orders():
            if row.get("tag") == tag:
                order = self._build_order(request, str(row["order_id"]))
                self._apply_broker_state(order, row)
                return order
        return None

    @staticmethod
    def _build_order(request: OrderRequest, broker_order_id: str) -> Order:
        return Order(request=request, status=OrderStatus.SUBMITTED, broker_order_id=broker_order_id)

    @staticmethod
    def _apply_broker_state(order: Order, row: dict[str, Any]) -> None:
        raw_status = str(row.get("status", "")).upper()
        order.status = _STATUS_MAP.get(raw_status, OrderStatus.SUBMITTED)
        if order.status is OrderStatus.REJECTED:
            order.rejection_reason = row.get("status_message") or "rejected by broker"
        filled_quantity = int(row.get("filled_quantity", 0))
        average_price = row.get("average_price")
        if filled_quantity > 0 and average_price and not order.fills:
            timestamp = row.get("exchange_timestamp") or row.get("order_timestamp")
            fill_time = timestamp if isinstance(timestamp, datetime) else datetime.now()
            order.fills.append(
                Fill(
                    fill_id=f"{order.broker_order_id}-recovered",
                    quantity=filled_quantity,
                    price=Decimal(str(average_price)),
                    timestamp=fill_time,
                )
            )
