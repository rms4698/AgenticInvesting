"""Paper (simulated) broker for shadow trading without live capital.

Fills are simulated immediately at a caller-supplied price (e.g. the latest
quote or last traded price). This adapter never contacts a real broker.
"""

from datetime import datetime, timezone
from decimal import Decimal
from itertools import count

from .models import Fill, Order, OrderRequest, OrderSide, OrderStatus, Position


class PaperBroker:
    """In-memory simulated broker with idempotent order placement.

    Risk-safety principle: rejects orders that would produce negative cash or
    a short position outright, since this platform is long-only by design
    (see the risk charter: no leverage, no shorting in the current phase).
    """

    def __init__(self, initial_cash: Decimal, commission_rate: Decimal = Decimal("0.0003")) -> None:
        if initial_cash < 0:
            raise ValueError("initial_cash cannot be negative")
        if commission_rate < 0:
            raise ValueError("commission_rate cannot be negative")
        self._cash = initial_cash
        self._commission_rate = commission_rate
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._fill_ids = count(1)

    def place_order(
        self,
        request: OrderRequest,
        *,
        fill_price: Decimal,
        timestamp: datetime | None = None,
    ) -> Order:
        """Submit and immediately simulate-fill a market order.

        Idempotent: a repeated ``client_order_id`` returns the existing order
        unchanged rather than filling again.
        """

        existing = self._orders.get(request.client_order_id)
        if existing is not None:
            return existing

        if fill_price <= 0:
            raise ValueError("fill_price must be positive")
        now = timestamp or datetime.now(timezone.utc)
        order = Order(request=request, created_at=now, updated_at=now)
        self._orders[request.client_order_id] = order

        if request.side is OrderSide.BUY:
            self._fill_buy(order, fill_price, now)
        else:
            self._fill_sell(order, fill_price, now)
        return order

    def cancel_order(self, client_order_id: str) -> Order:
        order = self._orders.get(client_order_id)
        if order is None:
            raise KeyError(f"unknown client_order_id: {client_order_id}")
        if order.status.is_terminal:
            raise ValueError(f"cannot cancel a terminal order (status={order.status})")
        order.status = OrderStatus.CANCELLED
        order.updated_at = datetime.now(timezone.utc)
        return order

    def get_order(self, client_order_id: str) -> Order | None:
        return self._orders.get(client_order_id)

    def list_orders(self) -> tuple[Order, ...]:
        return tuple(self._orders.values())

    def list_positions(self) -> tuple[Position, ...]:
        return tuple(position for position in self._positions.values() if position.quantity != 0)

    def cash_balance(self) -> Decimal:
        return self._cash

    def _fill_buy(self, order: Order, fill_price: Decimal, timestamp: datetime) -> None:
        quantity = order.request.quantity
        gross_value = fill_price * quantity
        commission = gross_value * self._commission_rate
        total_cost = gross_value + commission
        if total_cost > self._cash:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = (
                f"insufficient cash: required {total_cost:.2f}, available {self._cash:.2f}"
            )
            order.updated_at = timestamp
            return

        self._cash -= total_cost
        order.fills.append(
            Fill(fill_id=f"F{next(self._fill_ids)}", quantity=quantity, price=fill_price, timestamp=timestamp, commission=commission)
        )
        order.status = OrderStatus.FILLED
        order.updated_at = timestamp
        self._apply_position_delta(order.request, quantity, fill_price)

    def _fill_sell(self, order: Order, fill_price: Decimal, timestamp: datetime) -> None:
        key = self._position_key(order.request.instrument, order.request.exchange)
        current = self._positions.get(key)
        available = current.quantity if current else 0
        quantity = order.request.quantity
        if quantity > available:
            order.status = OrderStatus.REJECTED
            order.rejection_reason = (
                f"insufficient position: requested {quantity}, available {available} (no shorting)"
            )
            order.updated_at = timestamp
            return

        gross_value = fill_price * quantity
        commission = gross_value * self._commission_rate
        self._cash += gross_value - commission
        order.fills.append(
            Fill(fill_id=f"F{next(self._fill_ids)}", quantity=quantity, price=fill_price, timestamp=timestamp, commission=commission)
        )
        order.status = OrderStatus.FILLED
        order.updated_at = timestamp
        self._apply_position_delta(order.request, -quantity, fill_price)

    def _apply_position_delta(self, request: OrderRequest, signed_quantity: int, price: Decimal) -> None:
        key = self._position_key(request.instrument, request.exchange)
        current = self._positions.get(key)
        if current is None:
            self._positions[key] = Position(request.instrument, request.exchange, signed_quantity, price)
            return

        new_quantity = current.quantity + signed_quantity
        if signed_quantity > 0 and current.quantity >= 0:
            total_cost = current.average_price * current.quantity + price * signed_quantity
            new_average = total_cost / new_quantity if new_quantity != 0 else current.average_price
        else:
            # Reducing or closing a long position keeps the existing average price.
            new_average = current.average_price
        self._positions[key] = Position(request.instrument, request.exchange, new_quantity, new_average)

    @staticmethod
    def _position_key(instrument: str, exchange: str) -> str:
        return f"{exchange.upper()}:{instrument.upper()}"
