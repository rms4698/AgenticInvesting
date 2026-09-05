"""Batch-fetch the approved portfolio universe from Kite.

This script is read-only with respect to the broker. It resolves every symbol
against Kite's current instrument master, refuses ambiguous/missing symbols,
backs up existing datasets, and then uses the same audited ingestion path as
the single-symbol fetch script.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from kiteconnect import KiteConnect

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agentic_investing.data.ingestion import ingest_historical_bars
from agentic_investing.data.providers.kite import KiteHistoricalDataProvider
from agentic_investing.logging_config import get_logger
from agentic_investing.portfolio import load_universe
from fetch_kite_history import _resolve_credentials

LOGGER = get_logger(__name__)
_CASH_EQUITY_SYMBOL = re.compile(r"^[A-Z0-9&]{1,20}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch the approved multi-instrument Kite portfolio universe")
    parser.add_argument("--universe", default="config/portfolio_universe.json")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2018, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date.today())
    parser.add_argument("--timeframe", choices=("1d", "1h", "15m", "5m", "1m"), default="1d")
    parser.add_argument("--output-dir", default="data/real")
    parser.add_argument("--limit", type=int, default=None, help="Override the universe batch limit")
    parser.add_argument("--offset", type=int, default=0, help="Skip this many discovered symbols before fetching")
    return parser.parse_args()


def _resolve_instrument_tokens(kite: KiteConnect, exchange: str, symbols: set[str]) -> dict[str, int]:
    rows = kite.instruments(exchange)
    matches: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    for row in rows:
        symbol = str(row.get("tradingsymbol", "")).upper()
        if symbol in matches:
            matches[symbol].append(row)
    resolved: dict[str, int] = {}
    for symbol, candidates in matches.items():
        if len(candidates) != 1:
            raise ValueError(f"expected exactly one {exchange}:{symbol} instrument, found {len(candidates)}")
        resolved[symbol] = int(candidates[0]["instrument_token"])
    return resolved


def _discover_symbols(kite: KiteConnect, universe, limit: int | None, offset: int) -> list[tuple[str, str, int]]:
    if offset < 0:
        raise ValueError("offset cannot be negative")
    exchange = universe.instruments[0].exchange if universe.instruments else "NSE"
    rows = kite.instruments(exchange)
    if universe.selection_mode == "all_equity":
        candidates = {
            str(row.get("tradingsymbol", "")).upper(): int(row["instrument_token"])
            for row in rows
            if str(row.get("instrument_type", "")).upper() == "EQ"
            and str(row.get("segment", "")).upper() == exchange
            and _CASH_EQUITY_SYMBOL.fullmatch(str(row.get("tradingsymbol", "")).upper()) is not None
        }
        symbols = sorted(candidates)
        batch_limit = limit or universe.max_instruments_per_run
        symbols = symbols[offset : offset + batch_limit]
        return [(symbol, exchange, candidates[symbol]) for symbol in symbols]

    symbols = [item for item in universe.instruments if item.enabled]
    tokens = _resolve_instrument_tokens(kite, exchange, {item.symbol for item in symbols})
    return [(item.symbol, item.exchange, tokens[item.symbol]) for item in symbols]


def _backup_dataset(output_dir: Path, exchange: str, symbol: str, timeframe: str) -> None:
    stem = f"{exchange.lower()}_{symbol.lower()}_{timeframe}"
    dataset = output_dir / f"{stem}.json"
    manifest = output_dir / f"{stem}.manifest.json"
    if not dataset.exists() and not manifest.exists():
        return
    backup_dir = output_dir / "_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for path in (dataset, manifest):
        if path.exists():
            shutil.copy2(path, backup_dir / f"{path.name}.{timestamp}.bak")


def main() -> int:
    args = parse_args()
    universe = load_universe(ROOT / args.universe)
    credentials = _resolve_credentials()
    if credentials is None:
        print("No fresh Kite credentials found. Run scripts/kite_login.py first.", file=sys.stderr)
        return 2
    api_key, access_token = credentials
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    discovered = _discover_symbols(kite, universe, args.limit, args.offset)
    provider = KiteHistoricalDataProvider(kite)
    output_dir = ROOT / args.output_dir
    failures = 0
    for symbol, exchange, instrument_token in discovered:
        _backup_dataset(output_dir, exchange, symbol, args.timeframe)
        LOGGER.info("universe_fetch_started exchange=%s symbol=%s", exchange, symbol)
        try:
            manifest = ingest_historical_bars(
                provider,
                instrument_token=instrument_token,
                symbol=symbol,
                exchange=exchange,
                timeframe=args.timeframe,
                start=args.start,
                end=args.end,
                output_dir=output_dir,
            )
        except ValueError as error:
            failures += 1
            LOGGER.warning("universe_fetch_skipped symbol=%s reason=%s", symbol, error)
            print(f"SKIPPED {exchange}:{symbol}: {error}", file=sys.stderr)
            continue
        LOGGER.info("universe_fetch_complete symbol=%s rows=%d", symbol, manifest.row_count)
        print(f"{exchange}:{symbol} rows={manifest.row_count} sha256={manifest.normalized_sha256}")
    if not discovered or failures == len(discovered):
        print("No universe instruments were fetched successfully.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
