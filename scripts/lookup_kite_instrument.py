"""Look up an NSE instrument token using the saved read-only Kite session.

This script never places orders. It only calls the public instrument-master
endpoint and prints matching rows for manual/automated confirmation.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
SCRIPTS = ROOT / "scripts"
for path in (SRC, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from fetch_kite_history import _resolve_credentials  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: lookup_kite_instrument.py <search-term> [exchange]", file=sys.stderr)
        return 2
    search_term = sys.argv[1].upper()
    exchange = sys.argv[2].upper() if len(sys.argv) > 2 else "NSE"

    credentials = _resolve_credentials()
    if credentials is None:
        print(
            "No valid Kite credentials found. Run: .\\.venv\\Scripts\\python.exe scripts\\kite_login.py",
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
    instruments = kite.instruments(exchange)

    matches = [
        row
        for row in instruments
        if search_term in str(row.get("tradingsymbol", "")).upper()
        or search_term in str(row.get("name", "")).upper()
    ]
    matches.sort(key=lambda row: str(row.get("tradingsymbol", "")))

    if not matches:
        print(f"No matches for {search_term!r} on {exchange}.", file=sys.stderr)
        return 1

    print(f"{'tradingsymbol':<20} {'instrument_token':<18} {'segment':<12} {'lot_size':<8} name")
    for row in matches[:50]:
        print(
            f"{row.get('tradingsymbol', ''):<20} {row.get('instrument_token', ''):<18} "
            f"{row.get('segment', ''):<12} {row.get('lot_size', ''):<8} {row.get('name', '')}"
        )
    if len(matches) > 50:
        print(f"... and {len(matches) - 50} more matches (narrow your search term).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
