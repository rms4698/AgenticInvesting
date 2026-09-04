import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data.ingestion import ingest_historical_bars
from agentic_investing.data.providers.kite import KiteHistoricalDataProvider


class FakeKiteClient:
    def __init__(self) -> None:
        self.calls = []

    def historical_data(self, instrument_token, from_date, to_date, interval, continuous=False, oi=False):
        self.calls.append((instrument_token, from_date, to_date, interval, continuous, oi))
        return [
            {"date": "2026-08-31T10:00:00+05:30", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000},
            {"date": "2026-09-01T10:00:00+05:30", "open": 101, "high": 103, "low": 100, "close": 102, "volume": 1100},
        ]


class KiteProviderTests(unittest.TestCase):
    def test_provider_is_read_only_and_normalizes_utc(self) -> None:
        client = FakeKiteClient()
        provider = KiteHistoricalDataProvider(client)
        bars, digest = provider.historical_bars(
            instrument_token=256265,
            symbol="NIFTYBEES",
            exchange="NSE",
            timeframe="1d",
            start=date(2026, 8, 31),
            end=date(2026, 9, 1),
        )

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].timestamp.isoformat(), "2026-08-31T04:30:00+00:00")
        # available_at must be strictly after timestamp by the interval's
        # duration (here, one day for daily bars) — never equal to it, which
        # would assert the whole candle's OHLC was knowable at the instant
        # the interval began.
        self.assertEqual(bars[0].available_at, bars[0].timestamp + timedelta(days=1))
        self.assertEqual(bars[0].close, Decimal("101"))
        self.assertEqual(len(digest), 64)
        self.assertEqual(client.calls[0][3], "day")

    def test_ingestion_writes_data_and_manifest_without_secrets(self) -> None:
        provider = KiteHistoricalDataProvider(FakeKiteClient())
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = ingest_historical_bars(
                provider,
                instrument_token=256265,
                symbol="NIFTYBEES",
                exchange="NSE",
                timeframe="1d",
                start=date(2026, 8, 31),
                end=date(2026, 9, 1),
                output_dir=temp_dir,
            )
            manifest_path = Path(temp_dir) / "nse_niftybees_1d.manifest.json"
            data_path = Path(temp_dir) / "nse_niftybees_1d.json"

            self.assertTrue(manifest.valid)
            self.assertTrue(manifest_path.exists())
            self.assertTrue(data_path.exists())
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["provider"], "zerodha-kite-connect")
            self.assertNotIn("api_key", manifest_path.read_text(encoding="utf-8").lower())

    def test_invalid_request_is_rejected(self) -> None:
        provider = KiteHistoricalDataProvider(FakeKiteClient())
        with self.assertRaises(ValueError):
            provider.historical_bars(
                instrument_token=0,
                symbol="NIFTYBEES",
                exchange="NSE",
                timeframe="1d",
                start=date(2026, 9, 1),
                end=date(2026, 8, 31),
            )


if __name__ == "__main__":
    unittest.main()
