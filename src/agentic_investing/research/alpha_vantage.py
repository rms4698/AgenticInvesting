"""Alpha Vantage client: news sentiment, fundamentals, and technical indicators.

This module is a plain, deterministic HTTP client — not an agent. It is one
of the "tools" the agent's reasoning step calls to gather context before
producing a ``TradeProposal``; it has no opinion, no decision-making, and no
path to placing an order. Selected over building a custom
news/fundamentals pipeline because Alpha Vantage already covers NSE/BSE
symbols (e.g. ``RELIANCE.BSE``), news sentiment, company fundamentals, and
50+ technical indicators in one API — see the research notes in
``/memories/repo/agentic-investing-notes.md`` for why this was chosen over
Zerodha's own MCP server (no risk gate) and NewsAPI (production-prohibited
free tier).

Free tier is rate-limited (25 requests/day by default, higher for verified
educational/open-source use) — callers should request the smallest useful
scope (one instrument, one function) per call and let the trade journal
cache/deduplicate across a day's watchlist rather than polling repeatedly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


class HttpGet(Protocol):
    """The one HTTP capability this client needs, kept abstract for testing."""

    def __call__(self, url: str, *, params: dict[str, Any], timeout: float) -> dict[str, Any]: ...


def _default_http_get(url: str, *, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    import requests

    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json()


class AlphaVantageError(RuntimeError):
    """Raised when Alpha Vantage returns an error payload or rate-limit notice."""


@dataclass(frozen=True, slots=True)
class NewsArticle:
    title: str
    summary: str
    url: str
    time_published: str
    source: str
    overall_sentiment_label: str
    overall_sentiment_score: float
    ticker_sentiment_score: float | None


@dataclass(frozen=True, slots=True)
class CompanyOverview:
    symbol: str
    name: str
    sector: str
    industry: str
    pe_ratio: str | None
    peg_ratio: str | None
    price_to_book_ratio: str | None
    profit_margin: str | None
    return_on_equity_ttm: str | None
    revenue_growth_yoy: str | None
    analyst_target_price: str | None
    week_52_high: str | None
    week_52_low: str | None


def to_alpha_vantage_symbol(instrument: str, exchange: str) -> str:
    """Map this platform's (instrument, exchange) pair to Alpha Vantage's symbol format.

    Alpha Vantage documents NSE symbols as bare (e.g. ``RELIANCE``) in some
    endpoints and ``SYMBOL.BSE`` for BSE; NSE support varies by endpoint, so
    callers needing NSE fundamentals should verify against a known-good
    symbol first. This mapping only handles the documented ``.BSE`` suffix
    convention explicitly; NSE is passed through unchanged.
    """

    exchange_upper = exchange.upper()
    if exchange_upper == "BSE":
        return f"{instrument.upper()}.BSE"
    return instrument.upper()


class AlphaVantageClient:
    """Thin, deterministic wrapper around Alpha Vantage's REST API."""

    base_url = "https://www.alphavantage.co/query"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        http_get: HttpGet = _default_http_get,
        timeout: float = 15.0,
    ) -> None:
        resolved_key = api_key or os.environ.get("ALPHA_VANTAGE_API_KEY")
        if not resolved_key:
            raise ValueError(
                "Alpha Vantage API key not provided. Pass api_key= or set ALPHA_VANTAGE_API_KEY."
            )
        self._api_key = resolved_key
        self._http_get = http_get
        self._timeout = timeout

    def _call(self, **params: Any) -> dict[str, Any]:
        payload = self._http_get(self.base_url, params={**params, "apikey": self._api_key}, timeout=self._timeout)
        if "Error Message" in payload:
            raise AlphaVantageError(payload["Error Message"])
        if "Note" in payload:
            raise AlphaVantageError(f"Alpha Vantage rate limit: {payload['Note']}")
        if "Information" in payload and not any(key not in ("Information",) for key in payload):
            raise AlphaVantageError(f"Alpha Vantage: {payload['Information']}")
        return payload

    def news_sentiment(self, *, instrument: str, exchange: str, limit: int = 20) -> tuple[NewsArticle, ...]:
        """Ticker-filtered news with sentiment scores, most recent first."""

        symbol = to_alpha_vantage_symbol(instrument, exchange)
        payload = self._call(function="NEWS_SENTIMENT", tickers=symbol, limit=str(limit), sort="LATEST")
        articles = []
        for item in payload.get("feed", []):
            ticker_score = None
            for ticker_sentiment in item.get("ticker_sentiment", []):
                if ticker_sentiment.get("ticker") == symbol:
                    ticker_score = float(ticker_sentiment.get("ticker_sentiment_score", 0.0))
                    break
            articles.append(
                NewsArticle(
                    title=item.get("title", ""),
                    summary=item.get("summary", ""),
                    url=item.get("url", ""),
                    time_published=item.get("time_published", ""),
                    source=item.get("source", ""),
                    overall_sentiment_label=item.get("overall_sentiment_label", "NEUTRAL"),
                    overall_sentiment_score=float(item.get("overall_sentiment_score", 0.0)),
                    ticker_sentiment_score=ticker_score,
                )
            )
        return tuple(articles)

    def company_overview(self, *, instrument: str, exchange: str) -> CompanyOverview | None:
        """Fundamental snapshot (ratios, margins, analyst targets). None if unavailable."""

        symbol = to_alpha_vantage_symbol(instrument, exchange)
        payload = self._call(function="OVERVIEW", symbol=symbol)
        if not payload or "Symbol" not in payload:
            return None
        return CompanyOverview(
            symbol=payload.get("Symbol", symbol),
            name=payload.get("Name", ""),
            sector=payload.get("Sector", ""),
            industry=payload.get("Industry", ""),
            pe_ratio=payload.get("PERatio"),
            peg_ratio=payload.get("PEGRatio"),
            price_to_book_ratio=payload.get("PriceToBookRatio"),
            profit_margin=payload.get("ProfitMargin"),
            return_on_equity_ttm=payload.get("ReturnOnEquityTTM"),
            revenue_growth_yoy=payload.get("QuarterlyRevenueGrowthYOY"),
            analyst_target_price=payload.get("AnalystTargetPrice"),
            week_52_high=payload.get("52WeekHigh"),
            week_52_low=payload.get("52WeekLow"),
        )

    def technical_indicator(
        self,
        *,
        instrument: str,
        exchange: str,
        function: str,
        interval: str = "daily",
        time_period: int = 14,
        series_type: str = "close",
    ) -> dict[str, dict[str, str]]:
        """Raw technical-indicator time series (e.g. function='RSI', 'MACD', 'ADX').

        Returns Alpha Vantage's date-keyed series dict unchanged — callers
        (the agent's tool layer) are responsible for interpreting values;
        this client does no financial reasoning of its own.
        """

        symbol = to_alpha_vantage_symbol(instrument, exchange)
        payload = self._call(
            function=function,
            symbol=symbol,
            interval=interval,
            time_period=str(time_period),
            series_type=series_type,
        )
        series_key = next((key for key in payload if key.startswith("Technical Analysis")), None)
        if series_key is None:
            return {}
        return payload[series_key]
