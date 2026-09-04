import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.journal import TradeJournal


def make_journal() -> TradeJournal:
    temp_dir = tempfile.mkdtemp()
    return TradeJournal(Path(temp_dir) / "journal.sqlite3")


class TradeJournalEntryTests(unittest.TestCase):
    def test_entries_persist_and_are_returned_most_recent_first(self) -> None:
        journal = make_journal()
        timestamp1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        timestamp2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

        journal.add_entry(category="ANALYSIS", message="first note", timestamp=timestamp1)
        journal.add_entry(category="ANALYSIS", message="second note", timestamp=timestamp2)

        entries = journal.recent_entries()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].message, "second note")
        self.assertEqual(entries[1].message, "first note")

    def test_entries_filtered_by_instrument(self) -> None:
        journal = make_journal()
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        journal.add_entry(
            category="DECISION", message="buy A", instrument="A", exchange="NSE", timestamp=timestamp
        )
        journal.add_entry(
            category="DECISION", message="buy B", instrument="B", exchange="NSE", timestamp=timestamp
        )

        entries = journal.recent_entries(instrument="A", exchange="NSE")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].message, "buy A")

    def test_empty_message_rejected(self) -> None:
        journal = make_journal()
        with self.assertRaises(ValueError):
            journal.add_entry(category="ANALYSIS", message="   ")

    def test_naive_timestamp_rejected(self) -> None:
        journal = make_journal()
        with self.assertRaises(ValueError):
            journal.add_entry(category="ANALYSIS", message="note", timestamp=datetime(2026, 1, 1))

    def test_data_payload_round_trips_as_json(self) -> None:
        journal = make_journal()
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        journal.add_entry(
            category="DECISION",
            message="proposal",
            data={"confidence": 0.8, "sources": ["news:x", "technical:RSI"]},
            timestamp=timestamp,
        )
        entry = journal.recent_entries()[0]
        self.assertEqual(entry.data["confidence"], 0.8)
        self.assertEqual(entry.data["sources"], ["news:x", "technical:RSI"])


class TradeJournalDailyPlanTests(unittest.TestCase):
    def test_plan_upserts_by_instrument_and_exchange(self) -> None:
        journal = make_journal()
        timestamp1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        timestamp2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

        journal.set_daily_plan(
            instrument="A", exchange="NSE", thesis="initial thesis", target_price="110", timestamp=timestamp1
        )
        journal.set_daily_plan(
            instrument="A", exchange="NSE", thesis="revised thesis", target_price="115", timestamp=timestamp2
        )

        plan = journal.get_daily_plan(instrument="A", exchange="NSE")
        self.assertIsNotNone(plan)
        assert plan is not None  # narrows for type checkers; assertIsNotNone already verified this
        self.assertEqual(plan.thesis, "revised thesis")
        self.assertEqual(plan.target_price, "115")
        self.assertEqual(plan.updated_at, timestamp2)

    def test_missing_plan_returns_none(self) -> None:
        journal = make_journal()
        self.assertIsNone(journal.get_daily_plan(instrument="NOPE", exchange="NSE"))

    def test_all_daily_plans_returns_every_instrument(self) -> None:
        journal = make_journal()
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        journal.set_daily_plan(instrument="A", exchange="NSE", thesis="thesis A", timestamp=timestamp)
        journal.set_daily_plan(instrument="B", exchange="NSE", thesis="thesis B", timestamp=timestamp)

        plans = journal.all_daily_plans()
        self.assertEqual(len(plans), 2)
        self.assertEqual({plan.instrument for plan in plans}, {"A", "B"})

    def test_empty_thesis_rejected(self) -> None:
        journal = make_journal()
        with self.assertRaises(ValueError):
            journal.set_daily_plan(instrument="A", exchange="NSE", thesis="   ")


class TradeJournalCrossThreadTests(unittest.TestCase):
    """Regression test for the MCP tool server's threading model.

    The MCP server dispatches each synchronous tool call onto a fresh worker
    thread (anyio.to_thread), so a single long-lived TradeJournal instance
    (as mcp_server.server keeps for the process lifetime) must tolerate
    being used from a different thread than the one that created it.
    """

    def test_add_entry_from_a_different_thread_than_creation(self) -> None:
        journal = make_journal()
        errors: list[Exception] = []

        def write_from_worker_thread() -> None:
            try:
                journal.add_entry(
                    category="ANALYSIS",
                    message="written from another thread",
                    timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        worker = threading.Thread(target=write_from_worker_thread)
        worker.start()
        worker.join()

        self.assertEqual(errors, [])
        entries = journal.recent_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].message, "written from another thread")


if __name__ == "__main__":
    unittest.main()
