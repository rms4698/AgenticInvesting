"""Load canonical bars from the JSON format written by `ingest_historical_bars`."""

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import Bar, Timeframe


def _parse_timestamp(value: str, field: str) -> datetime:
    """Parse and require a timezone-aware timestamp, normalized to UTC.

    Matches the guarantee made by ``data/csv.py``'s loader: every Bar in the
    system has an aware timestamp. Without this check, a hand-edited or
    future-produced JSON file with naive ISO strings would silently create
    naive Bar.timestamp/available_at values, inconsistent with CSV- and
    provider-sourced bars and unsafe to compare/sort against them.
    """

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f"{field} timestamp must include a timezone: {value!r}")
    return parsed.astimezone(timezone.utc)


def _parse_decimal(value: object, field: str) -> Decimal:
    """Parse a price field, requiring a string to avoid float precision loss.

    ``ingest_historical_bars`` always writes prices as ``str(bar.price)``, so
    the round-trip through this project's own writer is safe. But
    ``Decimal(row["open"])`` with no type check would silently accept a JSON
    numeric literal (parsed by ``json.loads`` as a Python ``float``) from a
    hand-edited or future-produced file, constructing a ``Decimal`` from that
    float's exact (and often surprising) binary representation — e.g.
    ``Decimal(123.45)`` is not ``Decimal("123.45")``. Requiring a string
    surfaces that mismatch immediately instead of silently corrupting prices.
    """

    if not isinstance(value, str):
        raise ValueError(
            f"{field} must be a JSON string to avoid float precision loss, got {type(value).__name__}: {value!r}"
        )
    return Decimal(value)


def load_bars_json(path: str | Path) -> list[Bar]:
    """Load canonical bars from an ingested JSON dataset file."""

    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    bars: list[Bar] = []
    for row in payload:
        timeframe: Timeframe = row["timeframe"]
        bars.append(
            Bar(
                instrument=row["instrument"],
                exchange=row["exchange"],
                timeframe=timeframe,
                timestamp=_parse_timestamp(row["timestamp"], "timestamp"),
                available_at=_parse_timestamp(row["available_at"], "available_at"),
                open=_parse_decimal(row["open"], "open"),
                high=_parse_decimal(row["high"], "high"),
                low=_parse_decimal(row["low"], "low"),
                close=_parse_decimal(row["close"], "close"),
                volume=int(row["volume"]),
            )
        )
    return bars
