"""Perform local Zerodha login and save the daily session outside the repository."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.auth import authenticate_kite


if __name__ == "__main__":
    session = authenticate_kite()
    print(f"Kite session created for API key {session.api_key}.")
    print("The access token was saved outside the repository in the local application-data directory.")
