import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.portfolio.liquidity import rank_liquid_instruments
from agentic_investing.research.web_collector import append_snapshot, collect_snapshot


class FakeResearchClient:
    def research_fundamentals(self, *, instrument: str, exchange: str) -> dict:
        return {
            "available_at": "2025-01-02T00:00:00+00:00",
            "source_urls": ["https://example.com/filing"],
            "sector": "TEST",
            "market_cap": "1000000",
            "pe_ratio": "20",
            "revenue_growth": "0.1",
            "return_on_equity": "0.12",
            "debt_to_equity": "0.2",
            "confidence": "HIGH",
            "notes": "verified test response",
        }


class WebCollectionTests(unittest.TestCase):
    def test_collect_and_append_source_aware_snapshot(self) -> None:
        snapshot = collect_snapshot(FakeResearchClient(), instrument="TEST", exchange="NSE")
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "snapshots.json"
            append_snapshot(path, snapshot)
            append_snapshot(path, snapshot)
            records = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["source_urls"], ["https://example.com/filing"])

            replacement = collect_snapshot(
                FakeResearchClient(),
                instrument="TEST",
                exchange="NSE",
                retrieved_at=datetime(2025, 1, 3, tzinfo=timezone.utc),
            )
            append_snapshot(path, replacement)
            self.assertEqual(len(json.loads(path.read_text(encoding="utf-8"))), 1)

    def test_low_confidence_or_missing_sources_is_rejected(self) -> None:
        class LowConfidenceClient:
            def research_fundamentals(self, *, instrument: str, exchange: str) -> dict:
                return {"available_at": "2025-01-02T00:00:00+00:00", "confidence": "LOW"}

        with self.assertRaises(ValueError):
            collect_snapshot(LowConfidenceClient(), instrument="TEST", exchange="NSE")

    def test_liquidity_ranking_reads_local_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            data_dir = Path(temp_dir)
            rows = []
            for index in range(25):
                timestamp = datetime(2025, 1, index + 1, tzinfo=timezone.utc)
                rows.append(
                    {
                        "instrument": "TEST",
                        "exchange": "NSE",
                        "timeframe": "1d",
                        "timestamp": timestamp.isoformat(),
                        "available_at": timestamp.isoformat(),
                        "open": "100",
                        "high": "101",
                        "low": "99",
                        "close": "100",
                        "volume": 1000 + index,
                    }
                )
            (data_dir / "nse_test_1d.json").write_text(json.dumps(rows), encoding="utf-8")
            ranked = rank_liquid_instruments(
                data_dir,
                top_n=1,
                max_staleness=timedelta(days=10000),
                now=datetime(2025, 2, 1, tzinfo=timezone.utc),
            )
            self.assertEqual(ranked[0].instrument, "TEST")


if __name__ == "__main__":
    unittest.main()
