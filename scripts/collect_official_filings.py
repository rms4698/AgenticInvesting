"""Collect fundamentals from an approved official-filing manifest without API keys."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.logging_config import get_logger
from agentic_investing.research.filings import (
    FilingManifestEntry,
    UrlLibFilingFetcher,
    collect_official_filing,
    write_snapshot,
)

LOGGER = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect fundamentals from approved official filing URLs")
    parser.add_argument("--manifest", required=True, help="JSON list of approved filing sources")
    parser.add_argument("--output", default="data/fundamentals/snapshots.json")
    parser.add_argument("--raw-dir", default="data/fundamentals/raw")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--max-bytes", type=int, default=10_000_000)
    parser.add_argument("--refresh", action="store_true", help="refetch instruments already in the output")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = ROOT / args.manifest
    entries = _load_manifest(manifest_path)
    fetcher = UrlLibFilingFetcher(timeout=args.timeout, max_bytes=args.max_bytes)
    raw_dir = ROOT / args.raw_dir
    completed = set() if args.refresh else _existing_keys(ROOT / args.output)
    success = 0
    failures = 0
    for entry in entries:
        key = (entry.exchange, entry.instrument)
        if key in completed:
            print(f"SKIPPED {entry.exchange}:{entry.instrument}: already collected")
            continue
        try:
            snapshot = collect_official_filing(entry, fetcher)
            raw_path = raw_dir / f"{entry.exchange.lower()}_{entry.instrument.lower()}_{snapshot.document_sha256}.bin"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            if not raw_path.exists():
                raw_path.write_bytes(snapshot.raw_body)
            write_snapshot(ROOT / args.output, snapshot)
            completed.add(key)
            success += 1
            LOGGER.info("official_filing_collected instrument=%s source=%s", entry.instrument, entry.source_kind)
            print(f"COLLECTED {entry.exchange}:{entry.instrument}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            LOGGER.exception("official_filing_collection_failed instrument=%s", entry.instrument)
            print(f"SKIPPED {entry.exchange}:{entry.instrument}: {error}", file=sys.stderr)
    print(f"SUMMARY collected={success} skipped={failures}")
    return 1 if failures else 0


def _load_manifest(path: Path) -> tuple[FilingManifestEntry, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("filing manifest must contain a JSON list")
    return tuple(FilingManifestEntry.from_mapping(item) for item in payload)


def _existing_keys(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("fundamentals snapshot file must contain a list")
    return {(str(row["exchange"]).upper(), str(row["instrument"])) for row in payload}


if __name__ == "__main__":
    raise SystemExit(main())
