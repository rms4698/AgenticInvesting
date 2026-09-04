import sys
import unittest
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.research import AlphaVantageClient, AlphaVantageError, to_alpha_vantage_symbol


class FakeHttpGet:
    """Deterministic stand-in for a real HTTP call, recording the last request."""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.last_params: dict[str, Any] | None = None
        self.last_url: str | None = None

    def __call__(self, url: str, *, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        self.last_url = url
        self.last_params = params
        return self.response


class SymbolMappingTests(unittest.TestCase):
    def test_bse_gets_suffix(self) -> None:
        self.assertEqual(to_alpha_vantage_symbol("RELIANCE", "BSE"), "RELIANCE.BSE")

    def test_nse_passes_through_unchanged(self) -> None:
        self.assertEqual(to_alpha_vantage_symbol("reliance", "NSE"), "RELIANCE")


class AlphaVantageClientTests(unittest.TestCase):
    def test_requires_an_api_key(self) -> None:
        with self.assertRaises(ValueError):
            AlphaVantageClient(api_key=None, http_get=FakeHttpGet({}))

    def test_news_sentiment_parses_ticker_specific_score(self) -> None:
        fake = FakeHttpGet(
            {
                "feed": [
                    {
                        "title": "Company beats earnings estimates",
                        "summary": "Strong quarter.",
                        "url": "https://example.com/a",
                        "time_published": "20260101T0900",
                        "source": "Example News",
                        "overall_sentiment_label": "Bullish",
                        "overall_sentiment_score": 0.35,
                        "ticker_sentiment": [
                            {"ticker": "RELIANCE", "ticker_sentiment_score": "0.42"},
                        ],
                    }
                ]
            }
        )
        client = AlphaVantageClient(api_key="test-key", http_get=fake)

        articles = client.news_sentiment(instrument="RELIANCE", exchange="NSE")

        self.assertEqual(len(articles), 1)
        self.assertEqual(articles[0].ticker_sentiment_score, 0.42)
        assert fake.last_params is not None  # a request was made, so this was recorded
        self.assertEqual(fake.last_params["function"], "NEWS_SENTIMENT")
        self.assertEqual(fake.last_params["tickers"], "RELIANCE")

    def test_error_message_raises(self) -> None:
        fake = FakeHttpGet({"Error Message": "invalid symbol"})
        client = AlphaVantageClient(api_key="test-key", http_get=fake)
        with self.assertRaises(AlphaVantageError):
            client.news_sentiment(instrument="BOGUS", exchange="NSE")

    def test_rate_limit_note_raises(self) -> None:
        fake = FakeHttpGet({"Note": "Thank you for using Alpha Vantage! ... limit ..."})
        client = AlphaVantageClient(api_key="test-key", http_get=fake)
        with self.assertRaises(AlphaVantageError):
            client.company_overview(instrument="RELIANCE", exchange="NSE")

    def test_company_overview_missing_symbol_returns_none(self) -> None:
        fake = FakeHttpGet({})
        client = AlphaVantageClient(api_key="test-key", http_get=fake)
        self.assertIsNone(client.company_overview(instrument="RELIANCE", exchange="NSE"))

    def test_company_overview_parses_fields(self) -> None:
        fake = FakeHttpGet(
            {
                "Symbol": "RELIANCE",
                "Name": "Reliance Industries",
                "Sector": "ENERGY",
                "Industry": "OIL & GAS",
                "PERatio": "24.5",
                "AnalystTargetPrice": "1500.0",
            }
        )
        client = AlphaVantageClient(api_key="test-key", http_get=fake)
        overview = client.company_overview(instrument="RELIANCE", exchange="NSE")
        self.assertIsNotNone(overview)
        assert overview is not None  # narrows for type checkers; assertIsNotNone already verified this
        self.assertEqual(overview.pe_ratio, "24.5")
        self.assertEqual(overview.analyst_target_price, "1500.0")

    def test_technical_indicator_extracts_series(self) -> None:
        fake = FakeHttpGet(
            {
                "Meta Data": {},
                "Technical Analysis: RSI": {
                    "2026-01-02": {"RSI": "65.1234"},
                    "2026-01-01": {"RSI": "58.4321"},
                },
            }
        )
        client = AlphaVantageClient(api_key="test-key", http_get=fake)
        series = client.technical_indicator(instrument="RELIANCE", exchange="NSE", function="RSI")
        self.assertEqual(series["2026-01-02"]["RSI"], "65.1234")
        assert fake.last_params is not None  # a request was made, so this was recorded
        self.assertEqual(fake.last_params["function"], "RSI")

    def test_technical_indicator_unknown_series_key_returns_empty(self) -> None:
        fake = FakeHttpGet({"Meta Data": {}})
        client = AlphaVantageClient(api_key="test-key", http_get=fake)
        series = client.technical_indicator(instrument="RELIANCE", exchange="NSE", function="RSI")
        self.assertEqual(series, {})


if __name__ == "__main__":
    unittest.main()
