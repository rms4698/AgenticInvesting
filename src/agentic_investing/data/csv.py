"""CSV ingestion for normalized OHLCV data."""

import csv
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .models import Bar

_REQUIRED_COLUMNS = {
    "instrument",
    "exchange",
    "timeframe",
    "timestamp",
    "available_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
}


def _parse_timestamp(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid {field} timestamp: {value!r}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} timestamp must include a timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _parse_decimal(value: str, field: str, row_number: int) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise ValueError(f"row {row_number}: invalid {field}: {value!r}") from error
    if not parsed.is_finite():
        raise ValueError(f"row {row_number}: {field} must be finite")
    return parsed


def load_bars(path: str | Path) -> tuple[list[Bar], str]:
    """Load canonical bars from CSV and return bars plus the file SHA-256."""

    source_path = Path(path)
    raw = source_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()

    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or ())
        missing = _REQUIRED_COLUMNS - columns
        if missing:
            missing_columns = ", ".join(sorted(missing))
            raise ValueError(f"missing required CSV columns: {missing_columns}")

        bars: list[Bar] = []
        for row_number, row in enumerate(reader, start=2):
            try:
                timeframe = row["timeframe"]
                if timeframe not in {"1d", "1h", "15m", "5m", "1m"}:
                    raise ValueError(f"unsupported timeframe: {timeframe!r}")
                volume = int(row["volume"])
            except (TypeError, ValueError) as error:
                raise ValueError(f"row {row_number}: invalid categorical or volume value") from error
            if volume < 0:
                raise ValueError(f"row {row_number}: volume cannot be negative")

            bars.append(
                Bar(
                    instrument=row["instrument"].strip(),
                    exchange=row["exchange"].strip().upper(),
                    timeframe=timeframe,  # type: ignore[arg-type]
                    timestamp=_parse_timestamp(row["timestamp"], "bar"),
                    available_at=_parse_timestamp(row["available_at"], "available_at"),
                    open=_parse_decimal(row["open"], "open", row_number),
                    high=_parse_decimal(row["high"], "high", row_number),
                    low=_parse_decimal(row["low"], "low", row_number),
                    close=_parse_decimal(row["close"], "close", row_number),
                    volume=volume,
                )
            )

    return bars, digest
