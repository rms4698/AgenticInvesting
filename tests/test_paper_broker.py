import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.execution import OrderRequest, OrderSide, OrderStatus, PaperBroker


class PaperBrokerTests(unittest.TestCase):
    def test_buy_order_fills_and_deducts_cash_with_commission(self) -> None:
        broker = PaperBroker(initial_cash=Decimal("10000"), commission_rate=Decimal("0.001"))
        request = OrderRequest("order-1", "TEST", "NSE", OrderSide.BUY, quantity=10)

        order = broker.place_order(request, fill_price=Decimal("100"))

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(order.filled_quantity, 10)
        expected_cash = Decimal("10000") - (Decimal("1000") + Decimal("1000") * Decimal("0.001"))
        self.assertEqual(broker.cash_balance(), expected_cash)
        positions = broker.list_positions()
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].quantity, 10)
        self.assertEqual(positions[0].average_price, Decimal("100"))

    def test_duplicate_client_order_id_is_idempotent(self) -> None:
        broker = PaperBroker(initial_cash=Decimal("10000"))
        request = OrderRequest("order-1", "TEST", "NSE", OrderSide.BUY, quantity=5)

        first = broker.place_order(request, fill_price=Decimal("100"))
        cash_after_first = broker.cash_balance()
        second = broker.place_order(request, fill_price=Decimal("999"))  # different price ignored

        self.assertIs(first, second)
        self.assertEqual(broker.cash_balance(), cash_after_first)
        self.assertEqual(len(broker.list_orders()), 1)

    def test_buy_rejected_when_cash_insufficient(self) -> None:
        broker = PaperBroker(initial_cash=Decimal("100"))
        request = OrderRequest("order-1", "TEST", "NSE", OrderSide.BUY, quantity=10)

        order = broker.place_order(request, fill_price=Decimal("100"))

        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertIn("insufficient cash", order.rejection_reason or "")
        self.assertEqual(broker.cash_balance(), Decimal("100"))
        self.assertEqual(broker.list_positions(), ())

    def test_sell_rejected_when_no_shorting(self) -> None:
        broker = PaperBroker(initial_cash=Decimal("10000"))
        request = OrderRequest("order-1", "TEST", "NSE", OrderSide.SELL, quantity=5)

        order = broker.place_order(request, fill_price=Decimal("100"))

        self.assertEqual(order.status, OrderStatus.REJECTED)
        self.assertIn("no shorting", order.rejection_reason or "")

    def test_sell_closes_position_and_credits_cash(self) -> None:
        broker = PaperBroker(initial_cash=Decimal("10000"), commission_rate=Decimal("0"))
        buy = OrderRequest("buy-1", "TEST", "NSE", OrderSide.BUY, quantity=10)
        broker.place_order(buy, fill_price=Decimal("100"))

        sell = OrderRequest("sell-1", "TEST", "NSE", OrderSide.SELL, quantity=10)
        order = broker.place_order(sell, fill_price=Decimal("110"))

        self.assertEqual(order.status, OrderStatus.FILLED)
        self.assertEqual(broker.cash_balance(), Decimal("10000") - Decimal("1000") + Decimal("1100"))
        self.assertEqual(broker.list_positions(), ())

    def test_cancel_terminal_order_raises(self) -> None:
        broker = PaperBroker(initial_cash=Decimal("10000"))
        request = OrderRequest("order-1", "TEST", "NSE", OrderSide.BUY, quantity=1)
        broker.place_order(request, fill_price=Decimal("100"))

        with self.assertRaises(ValueError):
            broker.cancel_order("order-1")

    def test_cancel_unknown_order_raises(self) -> None:
        broker = PaperBroker(initial_cash=Decimal("10000"))
        with self.assertRaises(KeyError):
            broker.cancel_order("does-not-exist")


if __name__ == "__main__":
    unittest.main()
