"""Explainable long-only Donchian breakout strategy."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from agentic_investing.data.models import Bar

from .sma_crossover import Signal


@dataclass(frozen=True, slots=True)
class DonchianBreakoutStrategy:
    """Enter above the prior high channel and exit below the prior low channel.

    The channel excludes the current bar's close, so the decision is
    available only after the current bar closes and is executed by the
    backtester/session on the next bar open. This avoids look-ahead bias.
    """

    lookback_period: int = 20

    def __post_init__(self) -> None:
        if self.lookback_period < 2:
            raise ValueError("lookback_period must be at least 2")

    @property
    def warmup_period(self) -> int:
        return self.lookback_period + 1

    def decide(self, bars: Sequence[Bar], index: int, *, holding: bool) -> Signal | None:
        if not 0 <= index < len(bars):
            raise ValueError("index must be within bars")
        if index < self.lookback_period:
            return None

        bar = bars[index]
        prior = bars[index - self.lookback_period : index]
        upper = max(item.high for item in prior)
        lower = min(item.low for item in prior)
        action: str | None = None
        if not holding and bar.close > upper:
            action = "BUY"
        elif holding and bar.close < lower:
            action = "SELL"
        if action is None:
            return None
        return Signal(
            instrument=bar.instrument,
            timestamp=bar.timestamp,
            action=action,
            close=bar.close,
            fast_average=Decimal(upper),
            slow_average=Decimal(lower),
        )
