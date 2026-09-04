"""Provider-agnostic historical-data ingestion workflow."""

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from .manifest import DatasetManifest
from .models import Bar
from .providers.base import HistoricalDataProvider
from .validation import validate_bars


def normalized_sha256(bars: Iterable[Bar]) -> str:
    """Hash canonical bar fields in order for reproducibility."""

    rows = []
    for bar in bars:
        rows.append(
            {
                "instrument": bar.instrument,
                "exchange": bar.exchange,
                "timeframe": bar.timeframe,
                "timestamp": bar.timestamp.isoformat(),
                "available_at": bar.available_at.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
            }
        )
    payload = json.dumps(rows, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def ingest_historical_bars(
    provider: HistoricalDataProvider,
    *,
    instrument_token: int = 0,
    symbol: str,
    exchange: str,
    timeframe: str,
    start: date,
    end: date,
    output_dir: str | Path,
) -> DatasetManifest:
    """Fetch, validate, persist, and manifest one historical dataset."""

    bars, raw_sha256 = provider.historical_bars(
        instrument_token=instrument_token,
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,  # type: ignore[arg-type]
        start=start,
        end=end,
    )
    source = f"{provider.provider_name}:{exchange}:{symbol}:{timeframe}"
    normalized_digest = normalized_sha256(bars)
    report = validate_bars(bars, source=source, dataset_sha256=normalized_digest)
    if not report.is_valid:
        codes = ", ".join(issue.code for issue in report.issues)
        raise ValueError(f"historical dataset failed validation: {codes}")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    data_path = destination / f"{exchange.lower()}_{symbol.lower()}_{timeframe}.json"
    data_path.write_text(
        json.dumps(
            [
                {
                    "instrument": bar.instrument,
                    "exchange": bar.exchange,
                    "timeframe": bar.timeframe,
                    "timestamp": bar.timestamp.isoformat(),
                    "available_at": bar.available_at.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": bar.volume,
                }
                for bar in bars
            ],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    manifest = DatasetManifest.from_report(
        provider=provider.provider_name,
        instrument=symbol,
        exchange=exchange,
        timeframe=timeframe,
        start=start,
        end=end,
        raw_sha256=raw_sha256,
        normalized_sha256=normalized_digest,
        report=report,
    )
    manifest.write_json(destination / f"{exchange.lower()}_{symbol.lower()}_{timeframe}.manifest.json")
    return manifest
