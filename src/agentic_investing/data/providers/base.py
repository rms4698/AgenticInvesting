"""Provider contracts for historical market data."""

from datetime import date
from typing import Protocol

from ..models import Bar, Timeframe


class HistoricalDataProvider(Protocol):
    """Read-only contract used by ingestion workflows."""

    provider_name: str

    def historical_bars(
        self,
        *,
        instrument_token: int = 0,
        symbol: str,
        exchange: str,
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> tuple[list[Bar], str]:
        """Return normalized bars and a source-response fingerprint."""
        ...
