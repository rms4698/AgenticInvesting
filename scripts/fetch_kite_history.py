"""Fetch and validate one read-only Kite historical-data dataset.

Credentials are resolved in this order:
    1. KITE_API_KEY / KITE_ACCESS_TOKEN environment variables (if both set)
    2. The saved local session from `scripts/kite_login.py`
       (%LOCALAPPDATA%\\AgenticInvesting\\kite-session.json)

This script never places orders and never writes credentials to disk beyond
the session file already managed by the login flow.
"""

import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.auth import KiteSession, load_session
from agentic_investing.data.ingestion import ingest_historical_bars
from agentic_investing.data.providers.kite import KiteHistoricalDataProvider

# Zerodha access tokens are invalidated daily at 6 AM IST (00:30 UTC).
_TOKEN_EXPIRY_HOUR_UTC = 0
_TOKEN_EXPIRY_MINUTE_UTC = 30


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch read-only Zerodha historical candles")
    parser.add_argument("--instrument-token", type=int, required=True)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="NSE")
    parser.add_argument("--timeframe", choices=("1d", "1h", "15m", "5m", "1m"), default="1d")
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output-dir", default="data/real")
    return parser.parse_args()


def _is_session_fresh(session: KiteSession, *, now: datetime | None = None) -> bool:
    """Return False once the session has crossed the next 00:30 UTC boundary."""

    current = now or datetime.now(timezone.utc)
    generated = session.generated_at
    expiry = generated.replace(
        hour=_TOKEN_EXPIRY_HOUR_UTC, minute=_TOKEN_EXPIRY_MINUTE_UTC, second=0, microsecond=0
    )
    if expiry <= generated:
        expiry += timedelta(days=1)
    return current < expiry


def _resolve_credentials() -> tuple[str, str] | None:
    """Resolve API key and access token from the environment, then the saved session."""

    env_api_key = os.environ.get("KITE_API_KEY")
    env_access_token = os.environ.get("KITE_ACCESS_TOKEN")
    if env_api_key and env_access_token:
        return env_api_key, env_access_token

    session = load_session()
    if session is None:
        return None
    if not _is_session_fresh(session):
        print(
            "Saved Kite session has expired (tokens are invalidated daily). "
            "Run: .\\.venv\\Scripts\\python.exe scripts\\kite_login.py",
            file=sys.stderr,
        )
        return None
    return session.api_key, session.access_token


def main() -> int:
    args = parse_args()
    credentials = _resolve_credentials()
    if credentials is None:
        print(
            "No valid Kite credentials found. Either set KITE_API_KEY and "
            "KITE_ACCESS_TOKEN, or run: .\\.venv\\Scripts\\python.exe scripts\\kite_login.py",
            file=sys.stderr,
        )
        return 2
    api_key, access_token = credentials

    try:
        from kiteconnect import KiteConnect
    except ImportError:
        print("Install the official kiteconnect Python package before using this CLI.", file=sys.stderr)
        return 2

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    provider = KiteHistoricalDataProvider(kite)
    manifest = ingest_historical_bars(
        provider,
        instrument_token=args.instrument_token,
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
