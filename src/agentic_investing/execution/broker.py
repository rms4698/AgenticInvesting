"""Broker adapter contract shared by paper and future live implementations.

Any broker adapter (paper today, Zerodha later) implements this protocol so
the order manager and risk gating work identically regardless of backend.
"""

from decimal import Decimal
from typing import Protocol

from .models import Order, OrderRequest, Position


class BrokerAdapter(Protocol):
    """Minimal broker surface needed for order placement and reconciliation."""

    def place_order(self, request: OrderRequest) -> Order:
        """Submit an order. Must be idempotent on ``client_order_id``:

        calling this twice with the same ``client_order_id`` must return the
        existing order rather than submitting a duplicate.
        """

    def cancel_order(self, client_order_id: str) -> Order:
        """Cancel a non-terminal order. Raises if the order is unknown."""

    def get_order(self, client_order_id: str) -> Order | None:
        """Look up an order by client order id."""

    def list_orders(self) -> tuple[Order, ...]:
        """Return all known orders for reconciliation."""

    def list_positions(self) -> tuple[Position, ...]:
        """Return current net positions for reconciliation."""

    def cash_balance(self) -> Decimal:
        """Return available cash."""
