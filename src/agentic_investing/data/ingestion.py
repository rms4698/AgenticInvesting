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


def _bar_rows(bars: Iterable[Bar]) -> list[dict[str, object]]:
    """Canonical JSON-serializable row representation, shared by hashing and writing.

    Using one shared function for both the hash and the on-disk write is
    what guarantees ``DatasetManifest.normalized_sha256`` always matches the
    actual file bytes — previously the hash was computed from a
    separately-constructed, differently-formatted (compact, sorted-key)
    payload than what was written to disk (indented, insertion-order keys),
    so re-hashing the on-disk file could never reproduce the manifest's
    recorded digest, defeating the manifest's integrity-verification purpose.
    """

    return [
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
    ]


def _serialize_bars(bars: Iterable[Bar]) -> bytes:
    """Serialize bars to the exact bytes written to disk.

    Used for both the on-disk dataset file and the manifest's
    ``normalized_sha256`` hash, so the two are always consistent — anyone
    re-hashing the on-disk file can verify it against the manifest.
    Previously the hash was computed from a separately-constructed, more
    compact (sorted-key, no indentation) payload than what was actually
    written (indented, insertion-order keys), so the two could never match.
    """

    return (json.dumps(_bar_rows(bars), indent=2) + "\n").encode("utf-8")


def normalized_sha256(bars: Iterable[Bar]) -> str:
    """Hash the bars' canonical on-disk JSON serialization for reproducibility."""

    return hashlib.sha256(_serialize_bars(bars)).hexdigest()


def _write_json_atomic(path: Path, payload: bytes) -> None:
    """Write bytes atomically via a temp file + rename.

    A crash, power loss, or killed process during a direct write to an
    existing dataset path could leave a truncated/corrupted file in place of
    a previously good dataset, with no fallback — the rename here ensures
    the destination is only ever fully-written content or the prior file.
    """

    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_bytes(payload)
    temp_path.replace(path)


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
    data_bytes = _serialize_bars(bars)
    normalized_digest = hashlib.sha256(data_bytes).hexdigest()
    report = validate_bars(bars, source=source, dataset_sha256=normalized_digest)
    if not report.is_valid:
        codes = ", ".join(issue.code for issue in report.issues)
        raise ValueError(f"historical dataset failed validation: {codes}")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    data_path = destination / f"{exchange.lower()}_{symbol.lower()}_{timeframe}.json"
    _write_json_atomic(data_path, data_bytes)
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
