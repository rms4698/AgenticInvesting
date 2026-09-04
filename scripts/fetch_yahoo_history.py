"""Fetch and validate one Yahoo Finance research dataset.

This is for exploratory daily-bar research, not broker-authoritative data or
live execution. The script makes no trading calls.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.data.ingestion import ingest_historical_bars
from agentic_investing.data.providers.yahoo import YahooFinanceDataProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch Yahoo Finance research candles")
    parser.add_argument("--symbol", required=True, help="Yahoo symbol, e.g. NIFTYBEES.NS or INFY.NS")
    parser.add_argument("--exchange", default="NSE")
    parser.add_argument("--timeframe", choices=("1d", "1h", "15m", "5m", "1m"), default="1d")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", default="data/yahoo")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    provider = YahooFinanceDataProvider()
    manifest = ingest_historical_bars(
        provider,
        instrument_token=0,
        symbol=args.symbol,
        exchange=args.exchange,
        timeframe=args.timeframe,
        start=args.start,
        end=args.end,
        output_dir=ROOT / args.output_dir,
    )
    print(f"Validated {manifest.row_count} rows from {manifest.provider}.")
    print(f"Normalized SHA-256: {manifest.normalized_sha256}")
    print(f"Output directory: {ROOT / args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
