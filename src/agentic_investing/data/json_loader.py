"""Load canonical bars from the JSON format written by `ingest_historical_bars`."""

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .models import Bar, Timeframe


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
                timestamp=datetime.fromisoformat(row["timestamp"]),
                available_at=datetime.fromisoformat(row["available_at"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
            )
        )
    return bars
