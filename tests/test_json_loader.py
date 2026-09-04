import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data import load_bars_json


def _write_dataset(temp_dir: str, rows: list[dict]) -> Path:
    path = Path(temp_dir) / "bars.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


class JsonLoaderTimezoneTests(unittest.TestCase):
    """Regression tests for missing timezone enforcement in load_bars_json.

    Before the fix, load_bars_json used datetime.fromisoformat directly with
    no awareness check, unlike data/csv.py's loader which explicitly rejects
    naive timestamps. A hand-edited or future-produced JSON file with naive
    ISO timestamp strings would silently create naive Bar.timestamp values,
    inconsistent with (and unsafe to compare/sort against) CSV- and
    provider-sourced bars, which are always timezone-aware.
    """

    def test_aware_timestamps_load_and_normalize_to_utc(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_dataset(
                temp_dir,
                [
                    {
                        "instrument": "TEST",
                        "exchange": "NSE",
                        "timeframe": "1d",
                        "timestamp": "2026-01-01T09:15:00+05:30",
                        "available_at": "2026-01-01T09:15:00+05:30",
                        "open": "100",
                        "high": "101",
                        "low": "99",
                        "close": "100.5",
                        "volume": 1000,
                    }
                ],
            )

            bars = load_bars_json(path)

            self.assertEqual(len(bars), 1)
            self.assertIsNotNone(bars[0].timestamp.tzinfo)
            self.assertEqual(bars[0].close, Decimal("100.5"))

    def test_naive_timestamp_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_dataset(
                temp_dir,
                [
                    {
                        "instrument": "TEST",
                        "exchange": "NSE",
                        "timeframe": "1d",
                        "timestamp": "2026-01-01T09:15:00",  # no timezone offset
                        "available_at": "2026-01-01T09:15:00",
                        "open": "100",
                        "high": "101",
                        "low": "99",
                        "close": "100.5",
                        "volume": 1000,
                    }
                ],
            )

            with self.assertRaises(ValueError):
                load_bars_json(path)


class JsonLoaderDecimalPrecisionTests(unittest.TestCase):
    """Regression test for silent float precision loss in price fields.

    Before the fix, a JSON file with numeric (non-string) price fields would
    be parsed via ``Decimal(value)`` where ``value`` is a Python float,
    silently producing a Decimal that reflects the float's binary rounding
    error rather than the intended decimal value (e.g. Decimal(100.1) !=
    Decimal("100.1")). The fix requires price fields to be JSON strings.
    """

    def test_numeric_price_field_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_dataset(
                temp_dir,
                [
                    {
                        "instrument": "TEST",
                        "exchange": "NSE",
                        "timeframe": "1d",
                        "timestamp": "2026-01-01T09:15:00+05:30",
                        "available_at": "2026-01-01T09:15:00+05:30",
                        "open": 100.1,  # numeric, not a string -> must be rejected
                        "high": 101,
                        "low": 99,
                        "close": 100.5,
                        "volume": 1000,
                    }
                ],
            )

            with self.assertRaises(ValueError):
                load_bars_json(path)


if __name__ == "__main__":
    unittest.main()
