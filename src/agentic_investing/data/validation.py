"""Deterministic quality checks for canonical market bars."""

from datetime import datetime
from typing import Iterable

from .models import Bar, DataQualityIssue, DataQualityReport


def _issue(
    code: str,
    message: str,
    bar: Bar | None = None,
) -> DataQualityIssue:
    return DataQualityIssue(
        code=code,
        message=message,
        instrument=bar.instrument if bar else None,
        timestamp=bar.timestamp if bar else None,
    )


def validate_bars(
    bars: Iterable[Bar],
    *,
    source: str,
    dataset_sha256: str = "",
) -> DataQualityReport:
    """Validate OHLCV bars for research use.

    Checks cover identity, chronology, availability, OHLC relationships, and
    non-negative volume. Duplicate keys and non-increasing timestamps are
    reported rather than silently repaired.
    """

    materialized = list(bars)
    issues: list[DataQualityIssue] = []
    if not materialized:
        issues.append(_issue("EMPTY_DATASET", "dataset contains no bars"))
    seen: set[tuple[str, str, str, datetime]] = set()
    previous: dict[tuple[str, str, str], datetime] = {}

    for bar in materialized:
        key_without_timestamp = (bar.instrument, bar.exchange, bar.timeframe)
        key = (*key_without_timestamp, bar.timestamp)

        if not bar.instrument:
            issues.append(_issue("EMPTY_INSTRUMENT", "instrument is empty", bar))
        if not bar.exchange:
            issues.append(_issue("EMPTY_EXCHANGE", "exchange is empty", bar))
        if key in seen:
            issues.append(_issue("DUPLICATE_BAR", "duplicate instrument/timeframe/timestamp", bar))
        seen.add(key)

        prior_timestamp = previous.get(key_without_timestamp)
        if prior_timestamp is not None and bar.timestamp <= prior_timestamp:
            issues.append(_issue("NON_INCREASING_TIME", "timestamps must be strictly increasing", bar))
        previous[key_without_timestamp] = bar.timestamp

        if bar.available_at < bar.timestamp:
            issues.append(_issue("LOOKAHEAD_RISK", "available_at precedes bar timestamp", bar))
        if any(value <= 0 for value in (bar.open, bar.high, bar.low, bar.close)):
            issues.append(_issue("NON_POSITIVE_PRICE", "OHLC prices must be positive", bar))
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            issues.append(_issue("INVALID_OHLC", "high/low does not contain open and close", bar))
        if bar.high < bar.low:
            issues.append(_issue("INVALID_RANGE", "high must be at least low", bar))
        if bar.volume < 0:
            issues.append(_issue("NEGATIVE_VOLUME", "volume cannot be negative", bar))

    return DataQualityReport(
        source=source,
        row_count=len(materialized),
        dataset_sha256=dataset_sha256,
        issues=tuple(issues),
    )
