"""Technical indicators calculated from canonical OHLCV bars."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from agentic_investing.data.models import Bar


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    instrument: str
    exchange: str
    timestamp: object
    close: Decimal
    sma_fast: Decimal
    sma_slow: Decimal
    rsi: Decimal
    atr: Decimal
    volume_ratio: Decimal


def _sma(values: Sequence[Decimal], period: int) -> Decimal:
    return sum(values[-period:], Decimal("0")) / Decimal(period)


def _rsi(closes: Sequence[Decimal], period: int) -> Decimal:
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    recent = changes[-period:]
    gains = sum((change for change in recent if change > 0), Decimal("0")) / Decimal(period)
    losses = sum((-change for change in recent if change < 0), Decimal("0")) / Decimal(period)
    if losses == 0:
        return Decimal("100") if gains > 0 else Decimal("50")
    return Decimal("100") - (Decimal("100") / (Decimal("1") + gains / losses))


def _atr(bars: Sequence[Bar], period: int) -> Decimal:
    ranges: list[Decimal] = []
    selected = bars[-(period + 1) :]
    for index in range(1, len(selected)):
        bar = selected[index]
        previous_close = selected[index - 1].close
        ranges.append(max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close)))
    return sum(ranges, Decimal("0")) / Decimal(period)


def calculate_technical_snapshot(
    bars: Sequence[Bar],
    index: int,
    *,
    fast_period: int = 20,
    slow_period: int = 50,
    rsi_period: int = 14,
    atr_period: int = 14,
    volume_period: int = 20,
) -> TechnicalSnapshot | None:
    """Calculate indicators using only bars through ``index``.

    The function returns ``None`` until every requested lookback is available.
    No future bar is read, making it suitable for walk-forward backtesting.
    """

    if not 0 <= index < len(bars):
        raise ValueError("index must be within bars")
    if min(fast_period, slow_period, rsi_period, atr_period, volume_period) < 1:
        raise ValueError("indicator periods must be positive")
    minimum = max(slow_period, rsi_period + 1, atr_period + 1, volume_period)
    if index + 1 < minimum:
        return None

    visible = bars[: index + 1]
    closes = [bar.close for bar in visible]
    volumes = [Decimal(bar.volume) for bar in visible]
    volume_average = _sma(volumes, volume_period)
    return TechnicalSnapshot(
        instrument=visible[-1].instrument,
        exchange=visible[-1].exchange,
        timestamp=visible[-1].timestamp,
        close=visible[-1].close,
        sma_fast=_sma(closes, fast_period),
        sma_slow=_sma(closes, slow_period),
        rsi=_rsi(closes, rsi_period),
        atr=_atr(visible, atr_period),
        volume_ratio=volumes[-1] / volume_average if volume_average else Decimal("0"),
    )
