"""Dataset manifests for reproducible historical-data runs."""

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from .models import DataQualityReport


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    """Immutable metadata describing one normalized dataset artifact."""

    provider: str
    instrument: str
    exchange: str
    timeframe: str
    start: date
    end: date
    retrieved_at: datetime
    raw_sha256: str
    normalized_sha256: str
    row_count: int
    valid: bool

    @classmethod
    def from_report(
        cls,
        *,
        provider: str,
        instrument: str,
        exchange: str,
        timeframe: str,
        start: date,
        end: date,
        raw_sha256: str,
        normalized_sha256: str,
        report: DataQualityReport,
    ) -> "DatasetManifest":
        return cls(
            provider=provider,
            instrument=instrument,
            exchange=exchange,
            timeframe=timeframe,
            start=start,
            end=end,
            retrieved_at=datetime.now(timezone.utc),
            raw_sha256=raw_sha256,
            normalized_sha256=normalized_sha256,
            row_count=report.row_count,
            valid=report.is_valid,
        )

    def write_json(self, path: str | Path) -> None:
        """Write a stable, human-readable manifest; never write credentials.

        Writes atomically via a temp file + rename, matching
        ``data/ingestion.py``'s dataset write — a crash or interruption
        mid-write must never leave a truncated manifest in place of a
        previously good one.
        """

        payload = asdict(self)
        payload["start"] = self.start.isoformat()
        payload["end"] = self.end.isoformat()
        payload["retrieved_at"] = self.retrieved_at.isoformat()
        destination = Path(path)
        temp_path = destination.with_suffix(destination.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp_path.replace(destination)
