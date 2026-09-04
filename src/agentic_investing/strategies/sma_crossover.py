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

    def decide(self, bars: Sequence[Bar], index: int, *, holding: bool) -> Signal | None:
        """Stateless single-bar decision, grounded in the caller's real position.

        ``holding`` must reflect the actual broker/portfolio position, not an
        internally remembered belief. This is what prevents the strategy's
        notion of "am I in a position?" from ever drifting away from reality:
        every call is independently told the truth, so a BUY that was
        proposed here but never actually filled (rejected by risk limits,
        insufficient cash, a data outage, etc.) cannot cause this strategy to
        skip a later, genuine buying opportunity — the next call will still
        see ``holding=False`` and correctly re-evaluate for a BUY.

        Returns ``None`` during indicator warmup or when no action applies.
        """

        if not 0 <= index < len(bars):
            raise ValueError("index must be within bars")
        if index + 1 < self.slow_period:
            return None
        bar = bars[index]
        closes = [item.close for item in bars[index + 1 - self.slow_period : index + 1]]
        fast = self._average(closes[-self.fast_period :])
        slow = self._average(closes)
        action: str | None = None
        if not holding and fast > slow:
            action = "BUY"
        elif holding and fast < slow:
            action = "SELL"
        if action is None:
            return None
        return Signal(
            instrument=bar.instrument,
            timestamp=bar.timestamp,
            action=action,
            close=bar.close,
            fast_average=fast,
            slow_average=slow,
        )

    def generate_signals(self, bars: Sequence[Bar], *, start_index: int = 0) -> list[Signal]:
        """Generate a full-history signal list, simulating holding forward from flat.

        This is a convenience/inspection method that assumes every proposed
        BUY/SELL actually fills. It is retained for direct strategy inspection
        and tests. Live execution paths (``Backtester``, ``ShadowTradingSession``)
        must call :meth:`decide` per bar with the *real* position instead, so
        that a blocked or unfilled order can never desync the strategy's
        belief from reality. ``start_index`` resets the simulated holding state
        at an evaluation boundary while retaining prior bars for indicator
        warmup.
        """

        if start_index < 0 or start_index >= len(bars):
            raise ValueError("start_index must be within bars")
        signals: list[Signal] = []
        holding = False
        for index in range(start_index, len(bars)):
            signal = self.decide(bars, index, holding=holding)
            if signal is not None:
                holding = signal.action == "BUY"
                signals.append(signal)
        return signals

