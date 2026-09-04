import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.execution import (
    KiteBrokerAdapter,
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderStore,
    derive_tag,
    reconcile_startup_state,
)


class FakeKiteClient:
    """Simulates Kite order placement/history for adapter replay tests."""

    def __init__(self) -> None:
        self._next_id = 1000
        self._orders: dict[str, dict] = {}
        self.place_calls: list[dict] = []
        # Test hooks:
        self.reject_next = False
        self.disconnect_next = False

    def place_order(self, **kwargs) -> str:
        self.place_calls.append(kwargs)
        if self.disconnect_next:
            self.disconnect_next = False
            raise ConnectionError("simulated network disconnect during order placement")
        order_id = str(self._next_id)
        self._next_id += 1
        status = "REJECTED" if self.reject_next else "COMPLETE"
        self.reject_next = False
        self._orders[order_id] = {
            "order_id": order_id,
            "tag": kwargs["tag"],
            "status": status,
            "status_message": "simulated rejection" if status == "REJECTED" else None,
            "filled_quantity": kwargs["quantity"] if status == "COMPLETE" else 0,
            "average_price": 100.0 if status == "COMPLETE" else 0,
            "exchange_timestamp": datetime.now(timezone.utc),
            "tradingsymbol": kwargs["tradingsymbol"],
            "exchange": kwargs["exchange"],
        }
        return order_id

    def cancel_order(self, variety: str, order_id: str) -> str:
        self._orders[order_id]["status"] = "CANCELLED"
        return order_id

    def orders(self) -> list[dict]:
        return list(self._orders.values())

    def order_history(self, order_id: str) -> list[dict]:
        row = self._orders.get(order_id)
        return [row] if row else []

    def positions(self) -> dict:
        return {"net": [], "day": []}

    def margins(self, segment=None) -> dict:
        return {"available": {"live_balance": 100000.0}}


def make_request(client_order_id: str = "order-1") -> OrderRequest:
    return OrderRequest(client_order_id, "TEST", "NSE", OrderSide.BUY, quantity=10)


class KiteBrokerAdapterHappyPathTests(unittest.TestCase):
    def test_place_order_fills_and_maps_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OrderStore(Path(temp_dir) / "orders.json")
            client = FakeKiteClient()
            adapter = KiteBrokerAdapter(client, store)

            order = adapter.place_order(make_request())

            self.assertEqual(order.status, OrderStatus.SUBMITTED)  # place_order doesn't fetch history
            self.assertIsNotNone(order.broker_order_id)
            self.assertEqual(len(client.place_calls), 1)
            self.assertEqual(client.place_calls[0]["transaction_type"], "BUY")
            self.assertEqual(client.place_calls[0]["product"], "CNC")

    def test_refresh_order_reflects_broker_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OrderStore(Path(temp_dir) / "orders.json")
            client = FakeKiteClient()
            adapter = KiteBrokerAdapter(client, store)
            adapter.place_order(make_request())

            refreshed = adapter.refresh_order("order-1")

            assert refreshed is not None
            self.assertEqual(refreshed.status, OrderStatus.FILLED)
            self.assertEqual(refreshed.filled_quantity, 10)
            self.assertEqual(refreshed.average_fill_price, Decimal("100.0"))


class KiteBrokerAdapterFailureTests(unittest.TestCase):
    def test_rejected_order_is_reflected_after_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OrderStore(Path(temp_dir) / "orders.json")
            client = FakeKiteClient()
            client.reject_next = True
            adapter = KiteBrokerAdapter(client, store)
            adapter.place_order(make_request())

            refreshed = adapter.refresh_order("order-1")

            assert refreshed is not None
            self.assertEqual(refreshed.status, OrderStatus.REJECTED)
            self.assertEqual(refreshed.rejection_reason, "simulated rejection")

    def test_cancel_non_terminal_order_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OrderStore(Path(temp_dir) / "orders.json")
            client = FakeKiteClient()
            adapter = KiteBrokerAdapter(client, store)
            adapter.place_order(make_request())

            cancelled = adapter.cancel_order("order-1")

            self.assertEqual(cancelled.status, OrderStatus.CANCELLED)

    def test_disconnect_during_placement_raises_and_leaves_attempting_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "orders.json"
            store = OrderStore(store_path)
            client = FakeKiteClient()
            client.disconnect_next = True
            adapter = KiteBrokerAdapter(client, store)

            with self.assertRaises(ConnectionError):
                adapter.place_order(make_request())

            entry = store.get("order-1")
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry.stage, "attempting")
            # Broker never actually received the order.
            self.assertEqual(client.orders(), [])


class KiteBrokerAdapterRestartRecoveryTests(unittest.TestCase):
    def test_retry_after_restart_recovers_existing_order_via_tag(self) -> None:
        """Simulates a process restart: a fresh adapter/in-memory cache, but
        the disk-backed OrderStore and the broker's order book (by tag) still
        remember the prior attempt. Retrying must recover state, not double-submit.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "orders.json"
            client = FakeKiteClient()

            # First "process": place the order successfully.
            first_store = OrderStore(store_path)
            first_adapter = KiteBrokerAdapter(client, first_store)
            first_adapter.place_order(make_request())
            self.assertEqual(len(client.place_calls), 1)

            # "Restart": brand-new adapter and store instance reading the same file.
            second_store = OrderStore(store_path)
            second_adapter = KiteBrokerAdapter(client, second_store)
            recovered = second_adapter.place_order(make_request())

            self.assertEqual(recovered.status, OrderStatus.FILLED)
            self.assertEqual(len(client.place_calls), 1)  # no duplicate submission

    def test_retry_after_crash_before_broker_confirmation_submits_fresh_order(self) -> None:
        """If the store shows 'attempting' but the broker has no record at all
        (crash before the broker ever received the request), a fresh
        submission using the same tag must be allowed.
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            store_path = Path(temp_dir) / "orders.json"
            client = FakeKiteClient()

            # Simulate a crash: begin_attempt was persisted, but place_order
            # never reached the broker (e.g. process killed mid-call).
            crashed_store = OrderStore(store_path)
            crashed_store.begin_attempt("order-1")
            self.assertEqual(client.orders(), [])

            recovered_store = OrderStore(store_path)
            adapter = KiteBrokerAdapter(client, recovered_store)
            order = adapter.place_order(make_request())

            self.assertEqual(order.status, OrderStatus.SUBMITTED)
            self.assertEqual(len(client.place_calls), 1)
            self.assertEqual(client.place_calls[0]["tag"], derive_tag("order-1"))


class ReconciliationTests(unittest.TestCase):
    def test_reconcile_detects_position_and_cash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OrderStore(Path(temp_dir) / "orders.json")
            client = FakeKiteClient()
            adapter = KiteBrokerAdapter(client, store)

            report = reconcile_startup_state(
                adapter,
                expected_positions={"NSE:TEST": ("TEST", 10)},
                expected_cash=Decimal("50000"),
            )

            self.assertFalse(report.is_clean)
            self.assertTrue(any("NSE:TEST" in issue for issue in report.position_mismatches))
            self.assertIsNotNone(report.cash_mismatch)

    def test_reconcile_reports_clean_when_state_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = OrderStore(Path(temp_dir) / "orders.json")
            client = FakeKiteClient()
            adapter = KiteBrokerAdapter(client, store)

            report = reconcile_startup_state(
                adapter,
                expected_positions={},
                expected_cash=Decimal("100000"),
            )

            self.assertTrue(report.is_clean)


if __name__ == "__main__":
    unittest.main()
