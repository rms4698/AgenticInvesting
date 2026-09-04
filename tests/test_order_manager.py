import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.execution import OrderManager, OrderStatus, PaperBroker
from agentic_investing.risk import RiskEngine, RiskLimits


def make_manager(**limit_overrides) -> tuple[OrderManager, PaperBroker, RiskEngine]:
    limits = RiskLimits(account_capital=Decimal("100000"), **limit_overrides)
    broker = PaperBroker(initial_cash=Decimal("100000"), commission_rate=Decimal("0"))
    engine = RiskEngine(limits)
    manager = OrderManager(broker, engine)
    return manager, broker, engine


class OrderManagerBuyGatingTests(unittest.TestCase):
    def test_buy_is_sized_and_submitted_when_approved(self) -> None:
        manager, broker, engine = make_manager()
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)

        outcome = manager.submit_buy(
            client_order_id="order-1",
            instrument="TEST",
            exchange="NSE",
            equity=Decimal("100000"),
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )

        self.assertTrue(outcome.submitted)
        self.assertIsNotNone(outcome.order)
        assert outcome.order is not None  # narrows for type checkers; assertIsNotNone already verified this
        self.assertEqual(outcome.order.status, OrderStatus.FILLED)
        self.assertGreater(outcome.order.filled_quantity, 0)

    def test_buy_is_blocked_when_kill_switch_tripped(self) -> None:
        manager, broker, engine = make_manager()
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)
        engine.mark_to_market(Decimal("85000"), t0)  # trips 12% hard drawdown

        outcome = manager.submit_buy(
            client_order_id="order-1",
            instrument="TEST",
            exchange="NSE",
            equity=Decimal("85000"),
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )

        self.assertFalse(outcome.submitted)
        self.assertIsNone(outcome.order)
        self.assertTrue(any("kill switch" in reason for reason in outcome.reasons))
        self.assertEqual(broker.list_orders(), ())  # never reached the broker

    def test_buy_is_blocked_when_daily_loss_limit_breached(self) -> None:
        manager, broker, engine = make_manager(daily_loss_fraction=Decimal("0.01"))
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)

        outcome = manager.submit_buy(
            client_order_id="order-1",
            instrument="TEST",
            exchange="NSE",
            equity=Decimal("98000"),  # 2000 loss >= 1000 daily limit
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )

        self.assertFalse(outcome.submitted)
        self.assertTrue(any("daily loss" in reason for reason in outcome.reasons))
        self.assertEqual(broker.list_orders(), ())

    def test_duplicate_buy_client_order_id_is_idempotent_at_manager_level(self) -> None:
        manager, broker, engine = make_manager()
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)

        first = manager.submit_buy(
            client_order_id="order-1",
            instrument="TEST",
            exchange="NSE",
            equity=Decimal("100000"),
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )
        # Even though the kill switch trips afterward, a repeat submission of
        # the same client_order_id must short-circuit before any risk check.
        engine.mark_to_market(Decimal("85000"), t0)
        second = manager.submit_buy(
            client_order_id="order-1",
            instrument="TEST",
            exchange="NSE",
            equity=Decimal("85000"),
            fill_price=Decimal("999"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )

        assert first.order is not None and second.order is not None  # both submissions succeeded
        self.assertEqual(first.order.request.client_order_id, second.order.request.client_order_id)
        self.assertIs(first.order, second.order)
        self.assertEqual(len(broker.list_orders()), 1)


class OrderManagerSellGatingTests(unittest.TestCase):
    def test_sell_is_never_blocked_by_kill_switch(self) -> None:
        manager, broker, engine = make_manager()
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)
        manager.submit_buy(
            client_order_id="buy-1",
            instrument="TEST",
            exchange="NSE",
            equity=Decimal("100000"),
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )
        bought_quantity = broker.list_positions()[0].quantity

        engine.mark_to_market(Decimal("85000"), t0)  # trip kill switch while holding
        outcome = manager.submit_sell(
            client_order_id="sell-1",
            instrument="TEST",
            exchange="NSE",
            quantity=bought_quantity,
            fill_price=Decimal("85"),
        )

        self.assertTrue(outcome.submitted)
        assert outcome.order is not None  # a submitted sell always has an order
        self.assertEqual(outcome.order.status, OrderStatus.FILLED)
        self.assertEqual(broker.list_positions(), ())


class OrderManagerReconciliationTests(unittest.TestCase):
    def test_reconcile_reports_no_issues_for_terminal_orders(self) -> None:
        manager, broker, engine = make_manager()
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)
        manager.submit_buy(
            client_order_id="buy-1",
            instrument="TEST",
            exchange="NSE",
            equity=Decimal("100000"),
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )

        self.assertEqual(manager.reconcile(), ())


if __name__ == "__main__":
    unittest.main()
