"""Canonical order/fill/position models for paper and future live execution.

These models are broker-agnostic. Every field needed for reconciliation and
audit is explicit; nothing is inferred silently. Risk-safety principle: when
in doubt about state, the order manager fails closed (blocks new orders)
rather than guessing.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    """Order lifecycle states. Terminal states: FILLED, REJECTED, CANCELLED."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED)


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """A proposed order before any risk or broker involvement.

    ``client_order_id`` must be unique and stable for retries; it is the basis
    for idempotent duplicate-order protection.
    """

    client_order_id: str
    instrument: str
    exchange: str
    side: OrderSide
    quantity: int
    order_type: str = "MARKET"
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.client_order_id.strip():
            raise ValueError("client_order_id must not be empty")
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.order_type == "LIMIT" and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("limit_price must be positive for LIMIT orders")


@dataclass(frozen=True, slots=True)
class Fill:
    """One execution against an order. An order may have multiple fills."""

    fill_id: str
    quantity: int
    price: Decimal
    timestamp: datetime
    commission: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("fill quantity must be positive")
        if self.price <= 0:
            raise ValueError("fill price must be positive")
        if self.commission < 0:
            raise ValueError("commission cannot be negative")


@dataclass(slots=True)
class Order:
    """Mutable order record tracked by the broker adapter and order manager."""

    request: OrderRequest
    status: OrderStatus = OrderStatus.PENDING
    broker_order_id: str | None = None
    fills: list[Fill] = field(default_factory=list)
    rejection_reason: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def filled_quantity(self) -> int:
        return sum(fill.quantity for fill in self.fills)

    @property
    def remaining_quantity(self) -> int:
        return self.request.quantity - self.filled_quantity

    @property
    def average_fill_price(self) -> Decimal | None:
        if not self.fills:
            return None
        total_value = sum(fill.price * fill.quantity for fill in self.fills)
        total_quantity = sum(fill.quantity for fill in self.fills)
        return total_value / total_quantity


@dataclass(frozen=True, slots=True)
class Position:
    """A net open position in one instrument, derived from fills."""

    instrument: str
    exchange: str
    quantity: int
    average_price: Decimal
