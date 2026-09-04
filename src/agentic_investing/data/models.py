"""Canonical market-data models used by research and validation."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


Timeframe = Literal["1d", "1h", "15m", "5m", "1m"]


@dataclass(frozen=True, slots=True)
class Bar:
    """One OHLCV bar with explicit data availability metadata.

    ``available_at`` is when the platform was allowed to know the bar. Backtests
    must not use a bar before this timestamp, preventing look-ahead bias.
    """

    instrument: str
    exchange: str
    timeframe: Timeframe
    timestamp: datetime
    available_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class DataQualityIssue:
    """A validation issue tied to an instrument and timestamp when possible."""

    code: str
    message: str
    instrument: str | None = None
    timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """Validation result for a collection of canonical bars."""

    source: str
    row_count: int
    dataset_sha256: str
    issues: tuple[DataQualityIssue, ...]

    @property
    def is_valid(self) -> bool:
        return not self.issues
