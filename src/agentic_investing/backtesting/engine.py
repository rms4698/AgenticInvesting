"""Simple next-bar-open backtesting engine for research use."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Sequence

from agentic_investing.data.models import Bar
from agentic_investing.risk import RiskEngine, RiskLimits
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
    kill_switch_triggered: bool = False
    kill_switch_reason: str | None = None


@dataclass(slots=True)
class _OpenPosition:
    instrument: str
    entry_time: datetime
    quantity: int
    entry_price: Decimal
    entry_cost: Decimal


class Backtester:
    """Run a long-only strategy using next-bar open execution.

    Position sizing, kill-switch, and daily/monthly loss gating are delegated
    to a :class:`~agentic_investing.risk.RiskEngine` so backtests exercise the
    same deterministic risk decisions intended for later paper/live execution.
    """

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
        risk_engine = RiskEngine(self.risk_limits)
        cash = self.config.initial_capital
        equity_curve = [cash]
        position: _OpenPosition | None = None
        trades: list[Trade] = []

        for index in range(start_index, len(bars)):
            bar = bars[index]
            equity_before_bar = cash + (position.quantity * bar.open if position is not None else Decimal("0"))
            risk_engine.mark_to_market(equity_before_bar, bar.timestamp)

            # The decision is made on the previous (closed) bar, grounded in
            # the *real* position at that time, and executed at this bar's
            # open. Asking the strategy fresh each time — rather than trusting
            # a precomputed signal list — is what prevents the strategy's
            # notion of "holding" from ever drifting away from what actually
            # happened (e.g. a BUY that was proposed but blocked by risk
            # limits or insufficient cash never fools the strategy into
            # skipping a later, genuine buying opportunity).
            signal = strategy.decide(bars, index - 1, holding=position is not None) if index > 0 else None
            if signal and signal.action == "BUY" and position is None:
                decision = risk_engine.evaluate_new_position(equity=equity_before_bar, open_position_count=0)
                if decision.approved:
                    fill_price = bar.open * (Decimal("1") + self.config.slippage_rate)
                    quantity = risk_engine.size_new_position(
                        cash=cash,
                        fill_price=fill_price,
                        stop_distance_fraction=self.config.stop_distance_fraction,
                        initial_capital=self.config.initial_capital,
                        commission_rate=self.config.commission_rate,
                    )
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
            # Force-close at the last bar's close (the last known price),
            # with the same exit slippage every other exit pays, for
            # consistent cost modeling. This bar's equity_curve point was
            # already appended above as an *unrealized* mark-to-market value
            # (cash + quantity * close); replace that same point with the
            # *realized* post-liquidation cash value rather than appending a
            # second, spurious point for the same timestamp — appending would
            # corrupt period-return-based metrics (volatility/Sharpe) with an
            # extra "return" that does not correspond to any elapsed period.
            trade, cash = self._close_position(position, bars[-1], cash, at_open=False)
            trades.append(trade)
            equity_curve[-1] = cash

        metrics = calculate_metrics(
            initial_capital=self.config.initial_capital,
            final_capital=cash,
            equity_curve=equity_curve,
            trade_pnls=[trade.net_pnl for trade in trades],
            periods_per_year=self.config.periods_per_year,
        )
        return BacktestResult(
            self.config.initial_capital,
            cash,
            tuple(equity_curve),
            tuple(trades),
            metrics,
            kill_switch_triggered=risk_engine.kill_switch_triggered,
            kill_switch_reason=risk_engine.kill_switch_reason,
        )

    def _close_position(
        self,
        position: _OpenPosition,
        bar: Bar,
        cash: Decimal,
        *,
        at_open: bool = True,
    ) -> tuple[Trade, Decimal]:
        """Close a position and realize its trade.

        ``at_open=True`` (the normal, signal-driven exit) prices at this
        bar's open with slippage, matching next-bar-open execution timing.
        ``at_open=False`` is used only for a forced end-of-backtest
        liquidation, pricing at this bar's close with the same slippage —
        never at an unrealistically slippage-free close, and never mixing
        entry/exit timing bases within one trade.
        """

        exit_price = (bar.open if at_open else bar.close) * (Decimal("1") - self.config.slippage_rate)
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
