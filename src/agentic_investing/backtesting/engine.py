"""Simple next-bar-open backtesting engine for research use."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from typing import Sequence

from agentic_investing.data.models import Bar
from agentic_investing.risk import RiskLimits
from agentic_investing.strategies import SmaCrossoverStrategy

from .metrics import PerformanceMetrics, calculate_metrics


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_capital: Decimal = Decimal("100000")
    commission_rate: Decimal = Decimal("0.0003")
    slippage_rate: Decimal = Decimal("0.0005")
    stop_distance_fraction: Decimal = Decimal("0.05")
    periods_per_year: int = 252

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.commission_rate < 0 or self.slippage_rate < 0:
            raise ValueError("cost rates cannot be negative")
        if self.stop_distance_fraction <= 0 or self.stop_distance_fraction >= 1:
            raise ValueError("stop_distance_fraction must be between 0 and 1")
        if self.periods_per_year < 1:
            raise ValueError("periods_per_year must be positive")


@dataclass(frozen=True, slots=True)
class Trade:
    instrument: str
    entry_time: datetime
    exit_time: datetime
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    gross_pnl: Decimal
    total_cost: Decimal
    net_pnl: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    initial_capital: Decimal
    final_capital: Decimal
    equity_curve: tuple[Decimal, ...]
    trades: tuple[Trade, ...]
    metrics: PerformanceMetrics


@dataclass(slots=True)
class _OpenPosition:
    instrument: str
    entry_time: datetime
    quantity: int
    entry_price: Decimal
    entry_cost: Decimal


class Backtester:
    """Run a long-only strategy using next-bar open execution."""

    def __init__(self, config: BacktestConfig | None = None, risk_limits: RiskLimits | None = None) -> None:
        self.config = config or BacktestConfig()
        self.risk_limits = risk_limits or RiskLimits(account_capital=self.config.initial_capital)
        if self.risk_limits.account_capital != self.config.initial_capital:
            raise ValueError("risk limits capital must match backtest initial capital")

    def run(
        self,
        bars: Sequence[Bar],
        strategy: SmaCrossoverStrategy,
        *,
        start_index: int = 0,
    ) -> BacktestResult:
        """Backtest one instrument's bars using next-bar-open execution.

        ``start_index`` allows an evaluation period to use earlier bars for
        indicator warmup while preventing orders and returns before the chosen
        evaluation boundary from entering the result.
        """

        if not bars:
            raise ValueError("bars cannot be empty")
        if start_index < 0 or start_index >= len(bars):
            raise ValueError("start_index must be within bars")
        self._validate_input(bars)
        signals = {
            signal.timestamp: signal
            for signal in strategy.generate_signals(bars, start_index=start_index)
        }
        cash = self.config.initial_capital
        equity_curve = [cash]
        position: _OpenPosition | None = None
        trades: list[Trade] = []

        for index in range(start_index, len(bars)):
            bar = bars[index]
            signal = signals.get(bars[index - 1].timestamp) if index > 0 else None
            if signal and signal.action == "BUY" and position is None:
                fill_price = bar.open * (Decimal("1") + self.config.slippage_rate)
                quantity = self._entry_quantity(cash, fill_price)
                if quantity > 0:
                    entry_value = fill_price * quantity
                    entry_cost = entry_value * self.config.commission_rate
                    cash -= entry_value + entry_cost
                    position = _OpenPosition(bar.instrument, bar.timestamp, quantity, fill_price, entry_cost)
            elif signal and signal.action == "SELL" and position is not None:
                trade, cash = self._close_position(position, bar, cash)
                trades.append(trade)
                position = None

            marked_equity = cash
            if position is not None:
                marked_equity += position.quantity * bar.close
            equity_curve.append(marked_equity)

        if position is not None:
            trade, cash = self._close_position(position, bars[-1], cash, force_close=True)
            trades.append(trade)
            equity_curve.append(cash)

        metrics = calculate_metrics(
            initial_capital=self.config.initial_capital,
            final_capital=cash,
            equity_curve=equity_curve,
            trade_pnls=[trade.net_pnl for trade in trades],
            periods_per_year=self.config.periods_per_year,
        )
        return BacktestResult(self.config.initial_capital, cash, tuple(equity_curve), tuple(trades), metrics)

    def _entry_quantity(self, cash: Decimal, fill_price: Decimal) -> int:
        risk_budget = min(self.risk_limits.risk_per_trade, self.risk_limits.max_open_portfolio_risk)
        stop_distance = fill_price * self.config.stop_distance_fraction
        risk_quantity = int((risk_budget / stop_distance).to_integral_value(rounding=ROUND_DOWN))
        value_quantity = int((self.risk_limits.max_single_position_fraction * self.config.initial_capital / fill_price).to_integral_value(rounding=ROUND_DOWN))
        deployment_quantity = int((self.risk_limits.max_deployed_capital / fill_price).to_integral_value(rounding=ROUND_DOWN))
        cash_quantity = int((cash / (fill_price * (Decimal("1") + self.config.commission_rate))).to_integral_value(rounding=ROUND_DOWN))
        return max(0, min(risk_quantity, value_quantity, deployment_quantity, cash_quantity))

    def _close_position(
        self,
        position: _OpenPosition,
        bar: Bar,
        cash: Decimal,
        force_close: bool = False,
    ) -> tuple[Trade, Decimal]:
        del force_close  # Force-closing is represented by the final-bar event itself.
        exit_price = bar.open * (Decimal("1") - self.config.slippage_rate) if bar.timestamp != position.entry_time else bar.close
        exit_value = exit_price * position.quantity
        exit_cost = exit_value * self.config.commission_rate
        gross_pnl = (exit_price - position.entry_price) * position.quantity
        total_cost = position.entry_cost + exit_cost
        net_pnl = gross_pnl - total_cost
        cash += exit_value - exit_cost
        return Trade(
            instrument=position.instrument,
            entry_time=position.entry_time,
            exit_time=bar.timestamp,
            quantity=position.quantity,
            entry_price=position.entry_price,
            exit_price=exit_price,
            gross_pnl=gross_pnl,
            total_cost=total_cost,
            net_pnl=net_pnl,
        ), cash

    @staticmethod
    def _validate_input(bars: Sequence[Bar]) -> None:
        instrument = bars[0].instrument
        identity = (bars[0].exchange, bars[0].timeframe)
        previous = None
        for bar in bars:
            if bar.instrument != instrument or (bar.exchange, bar.timeframe) != identity:
                raise ValueError("backtest requires one instrument, exchange, and timeframe")
            if bar.available_at < bar.timestamp:
                raise ValueError("backtest input contains look-ahead risk")
            if previous is not None and bar.timestamp <= previous:
                raise ValueError("backtest bars must be strictly chronological")
            previous = bar.timestamp
            if min(bar.open, bar.high, bar.low, bar.close) <= 0:
                raise ValueError("backtest prices must be positive")
            if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
                raise ValueError("backtest input contains invalid OHLC data")
