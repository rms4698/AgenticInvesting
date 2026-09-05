"""Versioned approved portfolio-universe configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class UniverseInstrument:
    symbol: str
    exchange: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class PortfolioUniverse:
    version: str
    selection_mode: str
    max_instruments_per_run: int
    instruments: tuple[UniverseInstrument, ...]


def load_universe(path: str | Path) -> PortfolioUniverse:
    payload: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    exchange = str(payload.get("exchange", "NSE")).upper()
    version = str(payload["version"])
    selection_mode = str(payload.get("selection_mode", "explicit"))
    max_instruments_per_run = int(payload.get("max_instruments_per_run", 50))
    if selection_mode not in {"explicit", "all_equity"}:
        raise ValueError("selection_mode must be explicit or all_equity")
    if max_instruments_per_run < 1:
        raise ValueError("max_instruments_per_run must be positive")
    rows = payload.get("instruments")
    if rows is None and selection_mode == "all_equity":
        rows = []
    if not isinstance(rows, list) or (not rows and selection_mode == "explicit"):
        raise ValueError("explicit universes require a non-empty instruments list")

    instruments: list[UniverseInstrument] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        symbol = str(row["symbol"]).upper()
        key = (exchange, symbol)
        if key in seen:
            raise ValueError(f"duplicate universe instrument: {exchange}:{symbol}")
        seen.add(key)
        instruments.append(UniverseInstrument(symbol, exchange, bool(row.get("enabled", True))))
    return PortfolioUniverse(version, selection_mode, max_instruments_per_run, tuple(instruments))
