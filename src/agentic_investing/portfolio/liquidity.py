"""Liquidity ranking from local Kite OHLCV datasets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from agentic_investing.data.json_loader import load_bars_json


@dataclass(frozen=True, slots=True)
class LiquidityRank:
    instrument: str
    exchange: str
    last_timestamp: datetime
    average_volume: Decimal
    average_traded_value: Decimal
    last_close: Decimal


def rank_liquid_instruments(
    data_dir: str | Path,
    *,
    exchange: str = "NSE",
    timeframe: str = "1d",
    top_n: int = 200,
    volume_window: int = 20,
    max_staleness: timedelta = timedelta(days=10),
    now: datetime | None = None,
    as_of: datetime | None = None,
) -> tuple[LiquidityRank, ...]:
    """Rank locally ingested instruments by recent average traded volume.

    Stale datasets are excluded. This is a deterministic pre-filter, not a
    trading signal and not a guarantee of future liquidity.
    """

    if top_n < 1 or volume_window < 1:
        raise ValueError("top_n and volume_window must be positive")
    current = as_of or now or datetime.now(timezone.utc)
    ranks: list[LiquidityRank] = []
    for path in Path(data_dir).glob(f"{exchange.lower()}_*_{timeframe}.json"):
        bars = load_bars_json(path)
        if not bars:
            continue
        available_bars = [bar for bar in bars if bar.timestamp <= current]
        if not available_bars:
            continue
        last = available_bars[-1]
        if last.timestamp.tzinfo is None or current - last.timestamp > max_staleness:
            continue
        selected = available_bars[-volume_window:]
        average_volume = sum((Decimal(bar.volume) for bar in selected), Decimal("0")) / Decimal(len(selected))
        average_traded_value = (
            sum((bar.close * Decimal(bar.volume) for bar in selected), Decimal("0")) / Decimal(len(selected))
        )
        ranks.append(
            LiquidityRank(
                instrument=last.instrument,
                exchange=last.exchange,
                last_timestamp=last.timestamp,
                average_volume=average_volume,
                average_traded_value=average_traded_value,
                last_close=last.close,
            )
        )
    # Rank by rupee traded value (price x volume), not raw share count: a low-priced,
    # high-share-count penny stock can dominate a volume-only ranking while being far
    # less liquid in the rupee terms that actually matter for a real account's fills.
    ranks.sort(key=lambda item: (item.average_traded_value, item.instrument), reverse=True)
    return tuple(ranks[:top_n])
