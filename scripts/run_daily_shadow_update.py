"""Daily driver: refresh one Kite historical dataset, then replay it through
ShadowTradingSession end-to-end, archiving a dated operator report.

Design choice (deliberate, safety-first): this script does NOT persist any
shadow-session state (cash, position, history) between runs. Every run:

    1. Re-fetches the FULL historical range from Kite (reusing the already
       audited ``ingest_historical_bars`` path unchanged) and atomically
       overwrites the dataset + manifest under ``--output-dir``.
    2. Rebuilds a brand-new ``ShadowTradingSession`` and replays every bar
       in the refreshed dataset from the start, in order.
    3. Writes a dated daily report plus a "latest" pointer for quick review.

Rebuilding from scratch every run is intentionally more expensive than an
incremental update, but for a single EOD daily bar on one instrument it costs
a handful of Kite API calls and well under a second of local computation.
In exchange, it is impossible for this script to accumulate desynced or
corrupted session state across days — the same "ground truth over memory"
principle already enforced inside ShadowTradingSession itself. Run this
manually once after each trading day closes; Kite access tokens expire daily
and require an interactive browser login, so this is not wired to any
scheduler.
"""

import argparse
import shutil
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from kiteconnect import KiteConnect

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from agentic_investing.data import load_bars_json
from agentic_investing.data.ingestion import ingest_historical_bars
from agentic_investing.data.providers.kite import KiteHistoricalDataProvider
from agentic_investing.auth import authenticate_kite
from agentic_investing.shadow import ShadowSessionConfig, ShadowTradingSession
from agentic_investing.strategies import SmaCrossoverStrategy

from fetch_kite_history import _resolve_credentials  # noqa: E402


def _verify_instrument_token(kite: object, *, instrument_token: int, symbol: str, exchange: str) -> None:
    """Confirm instrument_token actually maps to symbol on Kite's instrument master.

    This exists because of a real incident: a placeholder/test instrument
    token (256265, the NIFTY 50 *index*) was passed by mistake where the
    NIFTYBEES *ETF* token (2707457) was intended, silently overwriting the
    real validated dataset with wrong-instrument data (the corruption was
    only caught by eyeballing an implausible price scale). A CLI flag typo
    or copy-paste from the wrong example must never be able to silently
    corrupt the dataset again.
    """

    instruments = kite.instruments(exchange)  # type: ignore[attr-defined]
    matches = [row for row in instruments if row.get("instrument_token") == instrument_token]
    if not matches:
        raise ValueError(
            f"instrument_token {instrument_token} was not found on {exchange}'s instrument master; "
            f"look it up with scripts/lookup_kite_instrument.py {symbol} {exchange}"
        )
    actual_symbol = str(matches[0].get("tradingsymbol", ""))
    if actual_symbol.upper() != symbol.upper():
        raise ValueError(
            f"instrument_token {instrument_token} maps to {actual_symbol!r} on {exchange}, "
            f"not {symbol!r} as requested — refusing to fetch/overwrite the dataset. "
            f"Look up the correct token with scripts/lookup_kite_instrument.py {symbol} {exchange}"
        )


def _backup_existing_dataset(output_dir: Path, exchange: str, symbol: str, timeframe: str) -> None:
    """Copy the existing dataset/manifest aside before overwriting, best-effort.

    Cheap insurance against a verification bug or an unexpected provider
    response corrupting the one on-disk copy of a real, previously-fetched
    dataset with no other backup (the file is gitignored).
    """

    stem = f"{exchange.lower()}_{symbol.lower()}_{timeframe}"
    backup_dir = output_dir / "_backups"
    for suffix in (".json", ".manifest.json"):
        source = output_dir / f"{stem}{suffix}"
        if source.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(source, backup_dir / f"{stem}{suffix}.{timestamp}.bak")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh a Kite dataset and replay it through a fresh shadow-trading session"
    )
    parser.add_argument(
        "--instrument-token",
        type=int,
        required=True,
        help=(
            "Kite instrument_token for the exact tradeable instrument (NOT an index token). "
            "Look it up with: scripts/lookup_kite_instrument.py <symbol> <exchange>. "
            "E.g. NIFTYBEES on NSE is 2707457, NOT the NIFTY 50 index token 256265."
        ),
    )
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--exchange", default="NSE")
    parser.add_argument("--timeframe", choices=("1d", "1h", "15m", "5m", "1m"), default="1d")
    parser.add_argument(
        "--history-start",
        type=date.fromisoformat,
        default=date(2018, 1, 1),
        help="Earliest date to fetch, matching the dataset's existing coverage (default: 2018-01-01)",
    )
    parser.add_argument("--fast-period", type=int, default=20)
    parser.add_argument("--slow-period", type=int, default=50)
    parser.add_argument("--output-dir", default="data/real", help="Dataset/manifest directory")
    parser.add_argument("--reports-dir", default="reports/shadow_daily", help="Dated daily-report archive directory")
    parser.add_argument(
        "--auto-login",
        action="store_true",
        help=(
            "If no fresh Kite session is found, open the interactive browser login flow "
            "automatically instead of exiting. Collapses the daily workflow into one command; "
            "still requires manual interaction in the browser (Zerodha login + TOTP)."
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    credentials = _resolve_credentials()
    if credentials is None and args.auto_login:
        print("No fresh Kite session found; starting interactive login...")
        session = authenticate_kite()
        credentials = (session.api_key, session.access_token)
    if credentials is None:
        print(
            "No valid Kite credentials found. Either set KITE_API_KEY and "
            "KITE_ACCESS_TOKEN, pass --auto-login, or run: "
            ".\\.venv\\Scripts\\python.exe scripts\\kite_login.py",
            file=sys.stderr,
        )
        return 2
    api_key, access_token = credentials

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    try:
        _verify_instrument_token(
            kite, instrument_token=args.instrument_token, symbol=args.symbol, exchange=args.exchange
        )
    except ValueError as error:
        print(f"Refusing to proceed: {error}", file=sys.stderr)
        return 2

    output_dir = ROOT / args.output_dir
    _backup_existing_dataset(output_dir, args.exchange, args.symbol, args.timeframe)

    provider = KiteHistoricalDataProvider(kite)
    today = datetime.now(timezone.utc).date()
    manifest = ingest_historical_bars(
        provider,
        instrument_token=args.instrument_token,
        symbol=args.symbol,
        exchange=args.exchange,
        timeframe=args.timeframe,
        start=args.history_start,
        end=today,
        output_dir=output_dir,
    )
    print(f"Refreshed {manifest.row_count} rows from {manifest.provider} (dataset valid: {manifest.valid}).")
    print(f"Normalized SHA-256: {manifest.normalized_sha256}")

    dataset_path = output_dir / f"{args.exchange.lower()}_{args.symbol.lower()}_{args.timeframe}.json"
    bars = load_bars_json(dataset_path)
    print(f"Replaying {len(bars)} bars through a fresh ShadowTradingSession...")

    strategy = SmaCrossoverStrategy(fast_period=args.fast_period, slow_period=args.slow_period)
    config = ShadowSessionConfig(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_rate=Decimal("0.0005"),
        stop_distance_fraction=Decimal("0.05"),
    )
    session = ShadowTradingSession(strategy=strategy, config=config)
    for bar in bars:
        session.on_bar(bar)

    report = session.daily_report()
    print()
    print(report)

    reports_dir = ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    last_bar_date = bars[-1].timestamp.date().isoformat() if bars else "no-data"
    dated_path = reports_dir / f"{last_bar_date}.md"
    dated_path.write_text(report, encoding="utf-8")
    latest_path = reports_dir / "latest.md"
    latest_path.write_text(report, encoding="utf-8")
    print(f"Report archived to {dated_path}")
    print(f"Latest report pointer updated at {latest_path}")

    if session.risk_engine.kill_switch_triggered:
        print(
            f"WARNING: kill switch is TRIPPED ({session.risk_engine.kill_switch_reason}). "
            "Review before considering any live pilot.",
            file=sys.stderr,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
