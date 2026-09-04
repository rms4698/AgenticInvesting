import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data.csv import load_bars
from agentic_investing.data.models import Bar
from agentic_investing.data.validation import validate_bars


class MarketDataTests(unittest.TestCase):
    def test_sample_dataset_loads_and_validates(self) -> None:
        fixture = Path(__file__).parents[1] / "data" / "sample_nse_ohlcv.csv"
        bars, digest = load_bars(fixture)
        report = validate_bars(bars, source=str(fixture), dataset_sha256=digest)

        self.assertEqual(len(bars), 5)
        self.assertEqual(len(digest), 64)
        self.assertTrue(report.is_valid)
        self.assertEqual(report.row_count, 5)

    def test_lookahead_and_ohlc_issues_are_reported(self) -> None:
        timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
        invalid_bar = Bar(
            instrument="BAD",
            exchange="NSE",
            timeframe="1d",
            timestamp=timestamp,
            available_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            open=Decimal("100"),
            high=Decimal("99"),
            low=Decimal("101"),
            close=Decimal("100"),
            volume=10,
        )

        report = validate_bars([invalid_bar], source="unit-test")
        issue_codes = {issue.code for issue in report.issues}

        self.assertFalse(report.is_valid)
        self.assertIn("LOOKAHEAD_RISK", issue_codes)
        self.assertIn("INVALID_OHLC", issue_codes)
        self.assertIn("INVALID_RANGE", issue_codes)

    def test_duplicate_and_non_increasing_timestamps_are_reported(self) -> None:
        timestamp = datetime(2026, 9, 1, tzinfo=timezone.utc)
        bars = [
            Bar("ABC", "NSE", "1d", timestamp, timestamp, Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), 1),
            Bar("ABC", "NSE", "1d", timestamp, timestamp, Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10"), 1),
        ]

        report = validate_bars(bars, source="unit-test")
        issue_codes = {issue.code for issue in report.issues}

        self.assertIn("DUPLICATE_BAR", issue_codes)
        self.assertIn("NON_INCREASING_TIME", issue_codes)

    def test_empty_dataset_is_invalid(self) -> None:
        report = validate_bars([], source="unit-test")

        self.assertFalse(report.is_valid)
        self.assertEqual(report.issues[0].code, "EMPTY_DATASET")


if __name__ == "__main__":
    unittest.main()
