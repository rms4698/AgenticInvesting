"""Simple long-only moving-average crossover baseline."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from agentic_investing.data.models import Bar


@dataclass(frozen=True, slots=True)
class Signal:
    """A decision generated after a bar closes and available for next-bar execution."""

    instrument: str
    timestamp: datetime
    action: str
    close: Decimal
    fast_average: Decimal
    slow_average: Decimal


@dataclass(frozen=True, slots=True)
class SmaCrossoverStrategy:
    """Long-only SMA crossover with no shorting or leverage."""

    fast_period: int = 3
    slow_period: int = 5

    def __post_init__(self) -> None:
        if self.fast_period < 1:
            raise ValueError("fast_period must be positive")
        if self.slow_period <= self.fast_period:
            raise ValueError("slow_period must be greater than fast_period")

    @staticmethod
    def _average(values: Sequence[Decimal]) -> Decimal:
        return sum(values, Decimal("0")) / Decimal(len(values))

    def generate_signals(self, bars: Sequence[Bar], *, start_index: int = 0) -> list[Signal]:
        """Generate crossover signals without using future bars.

        A signal at index ``i`` uses bars through ``i`` only. The backtester
        executes that signal at index ``i + 1``'s open. ``start_index`` resets
        the position state at an evaluation boundary while retaining prior bars
        for indicator warmup.
        """

        if start_index < 0 or start_index >= len(bars):
            raise ValueError("start_index must be within bars")
        signals: list[Signal] = []
        in_position = False
        for index in range(start_index, len(bars)):
            bar = bars[index]
            if index + 1 < self.slow_period:
                continue
            closes = [item.close for item in bars[index + 1 - self.slow_period : index + 1]]
            fast = self._average(closes[-self.fast_period :])
            slow = self._average(closes)
            action: str | None = None
            if not in_position and fast > slow:
                action = "BUY"
                in_position = True
            elif in_position and fast < slow:
                action = "SELL"
                in_position = False
            if action:
                signals.append(
                    Signal(
                        instrument=bar.instrument,
                        timestamp=bar.timestamp,
                        action=action,
                        close=bar.close,
                        fast_average=fast,
                        slow_average=slow,
                    )
                )
        return signals
