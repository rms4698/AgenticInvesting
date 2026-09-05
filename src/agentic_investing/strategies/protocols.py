"""Typed strategy contracts shared by backtesting and live/shadow sessions."""

from __future__ import annotations

from typing import Protocol, Sequence

from agentic_investing.data.models import Bar

from .sma_crossover import Signal


class TradingStrategy(Protocol):
    """A stateless, long-only strategy decision surface."""

    @property
    def warmup_period(self) -> int: ...

    def decide(self, bars: Sequence[Bar], index: int, *, holding: bool) -> Signal | None: ...
