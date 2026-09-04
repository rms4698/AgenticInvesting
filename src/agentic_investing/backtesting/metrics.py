"""Performance metrics for baseline backtests."""

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from statistics import stdev
from typing import Sequence


@dataclass(frozen=True, slots=True)
class PerformanceMetrics:
    """Basic metrics; returns are decimal fractions, e.g. 0.10 means 10%.

    ``profit_factor`` is ``0`` both when there were no trades at all and when
    there were only losing trades with zero gross profit — check
    ``trade_count`` to distinguish a flat/no-trade run from an all-losing run
    before comparing ``profit_factor`` values across runs (e.g. strategy vs.
    a no-trade cash benchmark).
    """

    total_return: Decimal
    max_drawdown: Decimal
    trade_count: int
    winning_trades: int
    losing_trades: int
    profit_factor: Decimal
    annualized_return: Decimal
    annualized_volatility: Decimal
    sharpe_ratio: Decimal


def _max_drawdown(equity_curve: Sequence[Decimal]) -> Decimal:
    if not equity_curve:
        return Decimal("0")
    peak = equity_curve[0]
    max_drawdown = Decimal("0")
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - equity) / peak)
    return max_drawdown


def calculate_metrics(
    *,
    initial_capital: Decimal,
    final_capital: Decimal,
    equity_curve: Sequence[Decimal],
    trade_pnls: Sequence[Decimal],
    periods_per_year: int = 252,
) -> PerformanceMetrics:
    """Calculate return, drawdown, volatility, and trade summary metrics."""

    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    if periods_per_year < 1:
        raise ValueError("periods_per_year must be positive")

    total_return = (final_capital - initial_capital) / initial_capital
    winning = [pnl for pnl in trade_pnls if pnl > 0]
    losing = [pnl for pnl in trade_pnls if pnl < 0]
    gross_profit = sum(winning, Decimal("0"))
    gross_loss = -sum(losing, Decimal("0"))
    if gross_loss == 0:
        profit_factor = Decimal("Infinity") if gross_profit else Decimal("0")
    else:
        profit_factor = gross_profit / gross_loss

    period_returns: list[float] = []
    for previous, current in zip(equity_curve, equity_curve[1:]):
        if previous <= 0:
            # Equity is expected to stay positive given upstream cash/position
            # constraints; silently skipping such a point (rather than
            # raising) would shrink the effective sample used for
            # volatility/Sharpe with no signal that anomalous data was
            # dropped. Surface it instead.
            raise ValueError(f"non-positive equity encountered in equity_curve: {previous}")
        period_returns.append(float((current - previous) / previous))
    # Sample standard deviation (N-1 denominator), not population stdev
    # (N denominator) — pstdev systematically understates volatility for the
    # small samples typical of a backtest, which in turn overstates the
    # resulting Sharpe ratio (mean / volatility).
    volatility = stdev(period_returns) * sqrt(periods_per_year) if len(period_returns) > 1 else 0.0
    mean_return = sum(period_returns) / len(period_returns) if period_returns else 0.0
    sharpe = (mean_return / (volatility / sqrt(periods_per_year))) * sqrt(periods_per_year) if volatility else 0.0

    periods = max(len(period_returns), 1)
    annualized = float((final_capital / initial_capital) ** (Decimal(periods_per_year) / Decimal(periods)) - 1)
    return PerformanceMetrics(
        total_return=total_return,
        max_drawdown=_max_drawdown(equity_curve),
        trade_count=len(trade_pnls),
        winning_trades=len(winning),
        losing_trades=len(losing),
        profit_factor=profit_factor,
        annualized_return=Decimal(str(annualized)),
        annualized_volatility=Decimal(str(volatility)),
        sharpe_ratio=Decimal(str(sharpe)),
    )
