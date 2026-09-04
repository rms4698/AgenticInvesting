"""Validated risk limits for the initial ₹1,00,000 research profile."""

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Account-level limits; execution code must reject values outside these limits."""

    account_capital: Decimal = Decimal("100000")
    capital_deployment_fraction: Decimal = Decimal("0.80")
    risk_per_trade_fraction: Decimal = Decimal("0.005")
    max_open_portfolio_risk_fraction: Decimal = Decimal("0.02")
    max_single_position_fraction: Decimal = Decimal("0.15")
    max_sector_exposure_fraction: Decimal = Decimal("0.25")
    daily_loss_fraction: Decimal = Decimal("0.01")
    monthly_loss_fraction: Decimal = Decimal("0.05")
    drawdown_review_fraction: Decimal = Decimal("0.08")
    hard_drawdown_fraction: Decimal = Decimal("0.12")
    max_leverage: Decimal = Decimal("1")
    minimum_reward_risk: Decimal = Decimal("1.5")
    max_positions: int = 8

    def __post_init__(self) -> None:
        fractions = {
            "capital_deployment_fraction": self.capital_deployment_fraction,
            "risk_per_trade_fraction": self.risk_per_trade_fraction,
            "max_open_portfolio_risk_fraction": self.max_open_portfolio_risk_fraction,
            "max_single_position_fraction": self.max_single_position_fraction,
            "max_sector_exposure_fraction": self.max_sector_exposure_fraction,
            "daily_loss_fraction": self.daily_loss_fraction,
            "monthly_loss_fraction": self.monthly_loss_fraction,
            "drawdown_review_fraction": self.drawdown_review_fraction,
            "hard_drawdown_fraction": self.hard_drawdown_fraction,
        }
        if self.account_capital <= 0:
            raise ValueError("account_capital must be positive")
        if any(value <= 0 or value > 1 for value in fractions.values()):
            raise ValueError("risk fractions must be greater than 0 and at most 1")
        if self.max_leverage < 1:
            raise ValueError("max_leverage must be at least 1")
        if self.minimum_reward_risk < 1:
            raise ValueError("minimum_reward_risk must be at least 1")
        if self.max_positions < 1:
            raise ValueError("max_positions must be positive")
        if self.risk_per_trade_fraction > self.max_open_portfolio_risk_fraction:
            raise ValueError("per-trade risk cannot exceed total open portfolio risk")
        if self.drawdown_review_fraction >= self.hard_drawdown_fraction:
            raise ValueError("review drawdown must be below hard drawdown")

    @property
    def max_deployed_capital(self) -> Decimal:
        return self.account_capital * self.capital_deployment_fraction

    @property
    def risk_per_trade(self) -> Decimal:
        return self.account_capital * self.risk_per_trade_fraction

    @property
    def max_open_portfolio_risk(self) -> Decimal:
        return self.account_capital * self.max_open_portfolio_risk_fraction

    @property
    def daily_loss_limit(self) -> Decimal:
        return self.account_capital * self.daily_loss_fraction

    @property
    def monthly_loss_limit(self) -> Decimal:
        return self.account_capital * self.monthly_loss_fraction

    @property
    def hard_drawdown_limit(self) -> Decimal:
        return self.account_capital * self.hard_drawdown_fraction
