"""Risk-gated order orchestration: the only path allowed to reach a broker.

This module enforces the platform's primary invariant: **no order reaches a
broker adapter without first passing the deterministic RiskEngine**. Minimize
risk first; maximize profit second — this ordering is structural, not just
documented.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from agentic_investing.risk import RiskEngine

from .broker import BrokerAdapter
from .models import Order, OrderRequest, OrderSide, OrderStatus


@dataclass(frozen=True, slots=True)
class OrderOutcome:
    """Result of a risk-gated order attempt, whether approved or blocked."""

    submitted: bool
    order: Order | None
    reasons: tuple[str, ...] = ()


class OrderManager:
    """Gates every order through a :class:`RiskEngine` before calling the broker.

    The manager is fail-closed: if the risk engine has not been marked to
    market for the current period, or reports any breach, no order is sent to
    the broker adapter.
    """

    def __init__(self, broker: BrokerAdapter, risk_engine: RiskEngine) -> None:
        self._broker = broker
        self._risk_engine = risk_engine

    def submit_buy(
        self,
        *,
        client_order_id: str,
        instrument: str,
        exchange: str,
        equity: Decimal,
        fill_price: Decimal,
        stop_distance_fraction: Decimal,
        initial_capital: Decimal,
        commission_rate: Decimal,
        timestamp: datetime | None = None,
    ) -> OrderOutcome:
        """Risk-check, size, and submit a BUY order. Never bypasses the risk engine."""

        existing = self._broker.get_order(client_order_id)
        if existing is not None:
            return OrderOutcome(submitted=True, order=existing)

        open_position_count = sum(1 for position in self._broker.list_positions() if position.quantity > 0)
        decision = self._risk_engine.evaluate_new_position(
            equity=equity, open_position_count=open_position_count
        )
        if not decision.approved:
            return OrderOutcome(submitted=False, order=None, reasons=decision.reasons)

        cash = self._broker.cash_balance()
        quantity = self._risk_engine.size_new_position(
            cash=cash,
            fill_price=fill_price,
            stop_distance_fraction=stop_distance_fraction,
            initial_capital=initial_capital,
            commission_rate=commission_rate,
        )
        if quantity <= 0:
            return OrderOutcome(submitted=False, order=None, reasons=("computed order quantity is zero",))

        request = OrderRequest(
            client_order_id=client_order_id,
            instrument=instrument,
            exchange=exchange,
            side=OrderSide.BUY,
            quantity=quantity,
        )
        order = self._place(request, fill_price=fill_price, timestamp=timestamp)
        return OrderOutcome(submitted=order.status != OrderStatus.REJECTED, order=order, reasons=self._rejection_reasons(order))

    def submit_sell(
        self,
        *,
        client_order_id: str,
        instrument: str,
        exchange: str,
        quantity: int,
        fill_price: Decimal,
        timestamp: datetime | None = None,
    ) -> OrderOutcome:
        """Submit a SELL (position-closing) order.

        Closing an existing position is always permitted regardless of the
        kill switch or loss limits — risk controls must never trap capital in
        a losing position by blocking exits.
        """

        existing = self._broker.get_order(client_order_id)
        if existing is not None:
            return OrderOutcome(submitted=True, order=existing)

        request = OrderRequest(
            client_order_id=client_order_id,
            instrument=instrument,
            exchange=exchange,
            side=OrderSide.SELL,
            quantity=quantity,
        )
        order = self._place(request, fill_price=fill_price, timestamp=timestamp)
        return OrderOutcome(submitted=order.status != OrderStatus.REJECTED, order=order, reasons=self._rejection_reasons(order))

    def reconcile(self) -> tuple[str, ...]:
        """Return human-readable discrepancies between expected and broker state.

        Currently checks for any non-terminal orders that should have resolved
        immediately in paper mode, flagging them for manual review.
        """

        issues: list[str] = []
        for order in self._broker.list_orders():
            if not order.status.is_terminal:
                issues.append(
                    f"order {order.request.client_order_id} is non-terminal (status={order.status})"
                )
        return tuple(issues)

    def _place(self, request: OrderRequest, *, fill_price: Decimal, timestamp: datetime | None) -> Order:
        """Place via the broker.

        ``fill_price``/``timestamp`` are always passed; ``BrokerAdapter``
        declares them as optional keyword-only parameters so simulation
        adapters (``PaperBroker``) can use them for deterministic fills while
        real broker adapters (``KiteBrokerAdapter``) accept and ignore them.
        """

        return self._broker.place_order(request, fill_price=fill_price, timestamp=timestamp)

    @staticmethod
    def _rejection_reasons(order: Order) -> tuple[str, ...]:
        if order.status == OrderStatus.REJECTED and order.rejection_reason:
            return (order.rejection_reason,)
        return ()
