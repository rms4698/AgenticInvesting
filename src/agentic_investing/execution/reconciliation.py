"""Startup and periodic reconciliation between internal and broker state.

Per the risk charter: uncertain broker state must block new orders rather
than be guessed at. This module never resolves a discrepancy automatically —
it only detects and reports them for manual review.
"""

from dataclasses import dataclass
from decimal import Decimal

from .broker import BrokerAdapter


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Discrepancies found between expected and actual broker state.

    An empty report (no issues in any field) means it is safe to resume
    trading. Any non-empty field should block new order submission until a
    human has reviewed it.
    """

    non_terminal_orders: tuple[str, ...] = ()
    position_mismatches: tuple[str, ...] = ()
    cash_mismatch: str | None = None

    @property
    def is_clean(self) -> bool:
        return not self.non_terminal_orders and not self.position_mismatches and self.cash_mismatch is None


def reconcile_startup_state(
    broker: BrokerAdapter,
    *,
    expected_positions: dict[str, tuple[str, int]] | None = None,
    expected_cash: Decimal | None = None,
    cash_tolerance: Decimal = Decimal("0.01"),
) -> ReconciliationReport:
    """Compare internally-expected state against the broker's reported state.

    ``expected_positions`` maps ``"EXCHANGE:INSTRUMENT"`` to
    ``(instrument, quantity)``. Pass ``None`` for either argument to skip that
    check (e.g. on a brand-new deployment with no prior expected state).
    """

    non_terminal: list[str] = []
    for order in broker.list_orders():
        if not order.status.is_terminal:
            non_terminal.append(
                f"order {order.request.client_order_id} is non-terminal (status={order.status})"
            )

    mismatches: list[str] = []
    if expected_positions is not None:
        actual = {f"{position.exchange.upper()}:{position.instrument.upper()}": position.quantity for position in broker.list_positions()}
        all_keys = set(expected_positions) | set(actual)
        for key in sorted(all_keys):
            expected_quantity = expected_positions.get(key, ("", 0))[1]
            actual_quantity = actual.get(key, 0)
            if expected_quantity != actual_quantity:
                mismatches.append(
                    f"position {key}: expected quantity {expected_quantity}, broker reports {actual_quantity}"
                )

    cash_issue: str | None = None
    if expected_cash is not None:
        actual_cash = broker.cash_balance()
        if abs(actual_cash - expected_cash) > cash_tolerance:
            cash_issue = f"cash mismatch: expected {expected_cash:.2f}, broker reports {actual_cash:.2f}"

    return ReconciliationReport(
        non_terminal_orders=tuple(non_terminal),
        position_mismatches=tuple(mismatches),
        cash_mismatch=cash_issue,
    )
