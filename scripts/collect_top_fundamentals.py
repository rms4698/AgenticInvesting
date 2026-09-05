"""Collect timestamped fundamentals for the top liquid local instruments.

This command delegates research to Claude native web search and stores only
validated source-aware snapshots. It does not scrape websites and does not
place orders. Existing snapshots are skipped unless --refresh is supplied.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.logging_config import get_logger
from agentic_investing.portfolio.liquidity import rank_liquid_instruments
from agentic_investing.research.web_collector import ClaudeWebResearchClient, append_snapshot, collect_snapshot

LOGGER = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect source-aware fundamentals for the most liquid local instruments")
    parser.add_argument("--data-dir", default="data/real")
    parser.add_argument("--output", default="data/fundamentals/snapshots.json")
    parser.add_argument("--top-n", type=int, default=200)
    parser.add_argument("--volume-window", type=int, default=20)
    parser.add_argument("--max-searches", type=int, default=5)
    parser.add_argument("--refresh", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ranked = rank_liquid_instruments(
        ROOT / args.data_dir,
        top_n=args.top_n,
        volume_window=args.volume_window,
    )
    client = ClaudeWebResearchClient(max_searches=args.max_searches)
    for rank in ranked:
        if not args.refresh and _snapshot_exists(ROOT / args.output, rank.instrument, rank.exchange):
            continue
        try:
            snapshot = collect_snapshot(client, instrument=rank.instrument, exchange=rank.exchange)
            append_snapshot(ROOT / args.output, snapshot)
            LOGGER.info("fundamentals_collected instrument=%s source=%s", rank.instrument, snapshot.source)
            print(f"COLLECTED {rank.exchange}:{rank.instrument}")
        except Exception as error:  # noqa: BLE001
            LOGGER.exception("fundamentals_collection_failed instrument=%s", rank.instrument)
            print(f"SKIPPED {rank.exchange}:{rank.instrument}: {error}", file=sys.stderr)
    return 0


def _snapshot_exists(path: Path, instrument: str, exchange: str) -> bool:
    if not path.exists():
        return False
    import json

    records = json.loads(path.read_text(encoding="utf-8"))
    return any(row.get("instrument") == instrument and row.get("exchange") == exchange for row in records)


if __name__ == "__main__":
    raise SystemExit(main())
