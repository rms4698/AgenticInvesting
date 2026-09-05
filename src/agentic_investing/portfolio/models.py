"""Versioned fundamentals, screening decisions, and portfolio plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from agentic_investing.features import TechnicalSnapshot


@dataclass(frozen=True, slots=True)
class FundamentalSnapshot:
    """Fundamental facts available to the strategy at a known timestamp."""

    instrument: str
    exchange: str
    available_at: datetime
    source: str
    sector: str
    market_cap: Decimal | None = None
    pe_ratio: Decimal | None = None
    revenue_growth: Decimal | None = None
    return_on_equity: Decimal | None = None
    debt_to_equity: Decimal | None = None

    def __post_init__(self) -> None:
        if self.available_at.tzinfo is None:
            raise ValueError("fundamental available_at must be timezone-aware")
        if not self.source.strip():
            raise ValueError("fundamental source must not be empty")
        if any(value is not None and value < 0 for value in (self.market_cap, self.pe_ratio, self.debt_to_equity)):
            raise ValueError("market_cap, pe_ratio, and debt_to_equity cannot be negative")


@dataclass(frozen=True, slots=True)
class ScreeningConfig:
    min_average_volume: int = 100_000
    min_market_cap: Decimal | None = None
    max_pe_ratio: Decimal | None = None
    min_revenue_growth: Decimal | None = None
    min_return_on_equity: Decimal | None = None
    max_debt_to_equity: Decimal | None = None
    minimum_reward_risk: Decimal = Decimal("1.5")
    stop_atr_multiple: Decimal = Decimal("2")
    max_positions: int = 8

    def __post_init__(self) -> None:
        if self.min_average_volume < 0:
            raise ValueError("min_average_volume cannot be negative")
        if self.min_market_cap is not None and self.min_market_cap < 0:
            raise ValueError("min_market_cap cannot be negative")
        if self.max_pe_ratio is not None and self.max_pe_ratio <= 0:
            raise ValueError("max_pe_ratio must be positive")
        if self.stop_atr_multiple <= 0 or self.minimum_reward_risk < 1:
            raise ValueError("stop_atr_multiple must be positive and reward/risk must be at least 1")
        if self.max_positions < 1:
            raise ValueError("max_positions must be positive")


@dataclass(frozen=True, slots=True)
class ScreenedCandidate:
    instrument: str
    exchange: str
    score: Decimal
    technical: TechnicalSnapshot
    fundamentals: FundamentalSnapshot
    stop_price: Decimal
    target_price: Decimal
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PortfolioDecision:
    instrument: str
    exchange: str
    action: str
    candidate: ScreenedCandidate | None
    reasons: tuple[str, ...]
