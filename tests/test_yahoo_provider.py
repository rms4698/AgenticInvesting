import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data.ingestion import ingest_historical_bars
from agentic_investing.data.providers.yahoo import YahooFinanceDataProvider


class FakeTimestamp:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def to_pydatetime(self) -> datetime:
        return self.value


class FakeRow:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = values

    def __getitem__(self, key: str) -> object:
        return self.values[key]


class FakeFrame:
    columns = ["Open", "High", "Low", "Close", "Volume"]
    empty = False

    def iterrows(self):
        yield FakeTimestamp(datetime(2024, 1, 2, 10, 0)), FakeRow(
            {"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1000}
        )
        yield FakeTimestamp(datetime(2024, 1, 3, 10, 0)), FakeRow(
            {"Open": 101, "High": 103, "Low": 100, "Close": 102, "Volume": 1100}
        )


class FakeDownloader:
    def __init__(self) -> None:
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeFrame()


class FakeSession:
    pass


class YahooProviderTests(unittest.TestCase):
    def test_provider_normalizes_naive_timestamps_and_maps_interval(self) -> None:
        downloader = FakeDownloader()
        session = FakeSession()
        provider = YahooFinanceDataProvider(downloader, session=session)
        bars, digest = provider.historical_bars(
            symbol="NIFTYBEES.NS",
            exchange="NSE",
            timeframe="1d",
            start=date(2024, 1, 1),
            end=date(2024, 1, 3),
        )

        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].timestamp, datetime(2024, 1, 2, 10, tzinfo=timezone.utc))
        self.assertEqual(bars[0].close, Decimal("101"))
        self.assertEqual(len(digest), 64)
        self.assertEqual(downloader.calls[0][1]["interval"], "1d")
        self.assertEqual(downloader.calls[0][1]["end"], "2024-01-04")
        self.assertIs(downloader.calls[0][1]["session"], session)

    def test_ingestion_writes_validated_manifest(self) -> None:
        provider = YahooFinanceDataProvider(FakeDownloader())
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = ingest_historical_bars(
                provider,
                symbol="NIFTYBEES.NS",
                exchange="NSE",
                timeframe="1d",
                start=date(2024, 1, 1),
                end=date(2024, 1, 3),
                output_dir=temp_dir,
            )

            self.assertEqual(manifest.provider, "yahoo-finance")
            self.assertTrue(manifest.valid)
            self.assertEqual(manifest.row_count, 2)

    def test_invalid_date_range_is_rejected(self) -> None:
        provider = YahooFinanceDataProvider(FakeDownloader())
        with self.assertRaises(ValueError):
            provider.historical_bars(
                symbol="NIFTYBEES.NS",
                exchange="NSE",
                timeframe="1d",
                start=date(2024, 1, 3),
                end=date(2024, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
