import hashlib
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data.ingestion import ingest_historical_bars
from agentic_investing.data.models import Bar


class FakeProvider:
    """Minimal HistoricalDataProvider stub returning two fixed, valid bars."""

    provider_name = "fake-provider"

    def historical_bars(self, *, instrument_token: int = 0, symbol, exchange, timeframe, start, end):
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = [
            Bar(
                instrument=symbol,
                exchange=exchange,
                timeframe=timeframe,
                timestamp=base + timedelta(days=offset),
                available_at=base + timedelta(days=offset + 1),
                open=Decimal("100"),
                high=Decimal("102"),
                low=Decimal("99"),
                close=Decimal("101"),
                volume=1000,
            )
            for offset in range(2)
        ]
        return bars, "raw-digest-placeholder"



class IngestionHashConsistencyTests(unittest.TestCase):
    """Regression: manifest's normalized_sha256 must match the on-disk file bytes.

    Before the fix, the hash was computed from a separately-constructed,
    differently-formatted payload (compact, sorted keys) than what was
    actually written to disk (indented, insertion order), so re-hashing the
    on-disk JSON file could never reproduce the manifest's recorded digest —
    defeating the manifest's entire integrity-verification purpose.
    """

    def test_rehashing_on_disk_file_matches_manifest_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest = ingest_historical_bars(
                FakeProvider(),
                symbol="TESTSYM",
                exchange="NSE",
                timeframe="1d",
                start=date(2026, 1, 1),
                end=date(2026, 1, 2),
                output_dir=temp_dir,
            )

            data_path = Path(temp_dir) / "nse_testsym_1d.json"
            on_disk_bytes = data_path.read_bytes()
            recomputed_digest = hashlib.sha256(on_disk_bytes).hexdigest()

            self.assertEqual(recomputed_digest, manifest.normalized_sha256)

    def test_no_leftover_tmp_file_after_successful_write(self) -> None:
        """Regression: atomic write via temp-file-then-rename leaves no .tmp behind."""

        with tempfile.TemporaryDirectory() as temp_dir:
            ingest_historical_bars(
                FakeProvider(),
                symbol="TESTSYM",
                exchange="NSE",
                timeframe="1d",
                start=date(2026, 1, 1),
                end=date(2026, 1, 2),
                output_dir=temp_dir,
            )

            leftover_tmp_files = list(Path(temp_dir).glob("*.tmp"))
            self.assertEqual(leftover_tmp_files, [])


if __name__ == "__main__":
    unittest.main()
