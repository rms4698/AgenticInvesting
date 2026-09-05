"""Load versioned, timestamp-aware fundamental snapshots from JSON."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .models import FundamentalSnapshot


def load_fundamentals_json(path: str | Path) -> dict[str, FundamentalSnapshot]:
    """Load a JSON list keyed by ``EXCHANGE:INSTRUMENT``.

    Values are strings for decimal fields and ISO-8601 timestamps with
    timezone offsets. The loader rejects malformed or naive timestamps rather
    than silently making a backtest use future information.
    """

    source_path = Path(path)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("fundamentals JSON must contain a list")

    snapshots: dict[str, FundamentalSnapshot] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("each fundamentals row must be an object")
        snapshot = FundamentalSnapshot(
            instrument=str(row["instrument"]),
            exchange=str(row["exchange"]).upper(),
            available_at=_parse_datetime(row["available_at"]),
            source=str(row["source"]),
            sector=str(row.get("sector", "UNKNOWN")),
            market_cap=_decimal_or_none(row.get("market_cap")),
            pe_ratio=_decimal_or_none(row.get("pe_ratio")),
            revenue_growth=_decimal_or_none(row.get("revenue_growth")),
            return_on_equity=_decimal_or_none(row.get("return_on_equity")),
            debt_to_equity=_decimal_or_none(row.get("debt_to_equity")),
        )
        key = f"{snapshot.exchange}:{snapshot.instrument}"
        if key in snapshots:
            raise ValueError(f"duplicate fundamentals snapshot for {key}")
        snapshots[key] = snapshot
    return snapshots


def _parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("fundamentals available_at must be an ISO-8601 string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("fundamentals available_at must be timezone-aware")
    return parsed


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("fundamentals decimal fields must be strings or null")
    return Decimal(value)
