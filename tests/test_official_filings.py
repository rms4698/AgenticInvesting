import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.portfolio.fundamentals import load_fundamentals_json
from agentic_investing.research.filings import (
    FetchedFiling,
    FilingManifestEntry,
    collect_official_filing,
    write_snapshot,
)


AVAILABLE_AT = datetime(2025, 1, 2, tzinfo=timezone.utc)


class FakeFetcher:
    def __init__(self, body: bytes, content_type: str) -> None:
        self.body = body
        self.content_type = content_type
        self.calls = 0

    def fetch(self, url: str) -> FetchedFiling:
        self.calls += 1
        return FetchedFiling(body=self.body, content_type=self.content_type)


def entry() -> FilingManifestEntry:
    return FilingManifestEntry(
        instrument="TEST",
        exchange="NSE",
        source_kind="nse",
        source_url="https://www.nseindia.com/official/test.json",
        available_at=AVAILABLE_AT,
        sector="TEST",
    )


class OfficialFilingsTests(unittest.TestCase):
    def test_json_filing_is_parsed_and_written_in_loader_format(self) -> None:
        body = json.dumps(
            {
                "facts": {
                    "market_cap": "1000000",
                    "pe_ratio": "20.5",
                    "revenue_growth": "0.12",
                    "return_on_equity": "0.15",
                    "debt_to_equity": "0.30",
                }
            }
        ).encode()
        fetcher = FakeFetcher(body, "application/json")
        snapshot = collect_official_filing(entry(), fetcher)

        self.assertEqual(fetcher.calls, 1)
        self.assertEqual(snapshot.fundamentals.pe_ratio, Decimal("20.5"))
        self.assertEqual(snapshot.document_sha256, hashlib.sha256(body).hexdigest())
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fundamentals.json"
            write_snapshot(path, snapshot)
            loaded = load_fundamentals_json(path)
            self.assertEqual(loaded["NSE:TEST"].debt_to_equity, Decimal("0.30"))

    def test_xml_aliases_are_supported(self) -> None:
        body = b"<facts><in-gaap:RevenueGrowth xmlns:in-gaap='urn:test'>0.2</in-gaap:RevenueGrowth></facts>"
        snapshot = collect_official_filing(entry(), FakeFetcher(body, "application/xml"))
        self.assertEqual(snapshot.fundamentals.revenue_growth, Decimal("0.2"))

    def test_csv_facts_are_supported(self) -> None:
        body = b"metric,value\nDebt to Equity,0.4\n"
        snapshot = collect_official_filing(entry(), FakeFetcher(body, "text/csv"))
        self.assertEqual(snapshot.fundamentals.debt_to_equity, Decimal("0.4"))

    def test_pdf_headline_extracts_only_consolidated_growth(self) -> None:
        class FakePage:
            def extract_text(self) -> str:
                return "Consolidated Revenue at INR 340,257 crore, up 24.5% Y-o-Y"

        class FakeReader:
            pages = [FakePage()]

        with patch("agentic_investing.research.filings.pypdf.PdfReader", return_value=FakeReader()):
            snapshot = collect_official_filing(entry(), FakeFetcher(b"%PDF-test", "application/pdf"))
        self.assertEqual(snapshot.fundamentals.revenue_growth, Decimal("0.245"))
        self.assertIsNone(snapshot.fundamentals.pe_ratio)

    def test_conflicting_values_and_non_official_urls_are_rejected(self) -> None:
        body = b'{"facts":{"pe_ratio":["10", "11"]}}'
        with self.assertRaisesRegex(ValueError, "conflicting"):
            collect_official_filing(entry(), FakeFetcher(body, "application/json"))
        with self.assertRaisesRegex(ValueError, "NSE filing URL"):
            FilingManifestEntry(
                instrument="TEST",
                exchange="NSE",
                source_kind="nse",
                source_url="https://example.com/test.json",
                available_at=AVAILABLE_AT,
            )


if __name__ == "__main__":
    unittest.main()
