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
    # Used ONLY for position sizing (RiskEngine.size_new_position): "if price
    # moves this far against me, how many shares keeps my loss at the
    # risk-per-trade budget?" It is deliberately NOT used as the actual
    # stop-loss trigger distance below — reusing it for both caused the
    # stop to fire on ordinary intraday/day-to-day noise for a multi-week
    # positional strategy (verified on real NIFTYBEES data: a 5% stop
    # produced 126 whipsaw trades and turned +9.68% into -2.95% return).
    stop_distance_fraction: Decimal = Decimal("0.05")
    # The ACTUAL stop-loss/target trigger distance from entry price. Wider
    # than stop_distance_fraction on purpose: sizing should assume a tight,
    # conservative loss-per-share for risk-budget math, while the real exit
    # needs enough room to avoid closing on normal volatility for this
    # strategy's holding period (weeks, per the 20/50-day SMA crossover).
    #
    # Chosen empirically against real NIFTYBEES data (2018-2026, SMA 20/50):
    # a tight 5% stop produced 81 whipsaw stop-outs and turned +9.68% into
    # -2.95% return. 20% avoids that (2 stop-outs) while still capping a
    # genuine adverse move. Walk-forward out-of-sample check: a stop-loss
    # here (any width tested, 15-20%) slightly REDUCES average out-of-sample
    # return versus no stop at all (6/13 positive windows vs. 7/13) in this
    # particular historical window, which had no real crash to protect
    # against. This is expected and accepted: a stop-loss is tail-risk
    # insurance, not a return enhancer, and "minimize risk first" is this
    # project's explicit standing priority over maximizing backtest return.
    stop_loss_distance_fraction: Decimal = Decimal("0.20")
    periods_per_year: int = 252
    enable_stop_loss: bool = True
    enable_target_exit: bool = True

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.commission_rate < 0 or self.slippage_rate < 0:
            raise ValueError("cost rates cannot be negative")
        if self.stop_distance_fraction <= 0 or self.stop_distance_fraction >= 1:
            raise ValueError("stop_distance_fraction must be between 0 and 1")
        if self.stop_loss_distance_fraction <= 0 or self.stop_loss_distance_fraction >= 1:
            raise ValueError("stop_loss_distance_fraction must be between 0 and 1")
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
    # "SIGNAL" (strategy-driven exit), "STOP_LOSS", "TARGET", or
    # "FORCED_LIQUIDATION" (position still open when the backtest data ends).
    # Defaulted so existing positional Trade(...) construction (e.g. in
    # evaluation.py's benchmark helpers) keeps working unchanged.
    exit_reason: str = "SIGNAL"


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
    stop_price: Decimal
    target_price: Decimal


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
                        stop_price = (
                            fill_price * (Decimal("1") - self.config.stop_loss_distance_fraction)
                            if self.config.enable_stop_loss
                            else Decimal("0")
                        )
                        target_price = (
                            fill_price
                            * (
                                Decimal("1")
                                + self.config.stop_loss_distance_fraction * self.risk_limits.minimum_reward_risk
                            )
                            if self.config.enable_target_exit
                            else Decimal("Infinity")
                        )
                        position = _OpenPosition(
                            bar.instrument, bar.timestamp, quantity, fill_price, entry_cost, stop_price, target_price
                        )

            # Stop-loss and profit-target are checked every bar a position is
            # held, using THIS bar's intrabar low/high — not just on bars
            # where the strategy happens to emit a SELL signal. A lagging
            # crossover signal could otherwise let a large adverse move ride
            # for days before the strategy itself decides to exit, which is
            # exactly the "minimize risk first" gap this closes. If both a
            # stop and a target could be read as hit on the same bar (a large
            # gap/whipsaw), the stop takes priority — risk-first, assume the
            # worse outcome rather than the better one.
            closed_by_stop_or_target = False
            if position is not None and (self.config.enable_stop_loss or self.config.enable_target_exit):
                stop_hit = self.config.enable_stop_loss and bar.low <= position.stop_price
                target_hit = self.config.enable_target_exit and bar.high >= position.target_price
                if stop_hit:
                    # A stop-loss is a market order once triggered: filled at
                    # the stop price, or worse (bar.open) if the bar gapped
                    # down through it overnight, then subject to the same
                    # execution slippage as any other exit — never at an
                    # unrealistically favorable price.
                    trigger_price = min(bar.open, position.stop_price)
                    exit_price = trigger_price * (Decimal("1") - self.config.slippage_rate)
                    trade, cash = self._close_position(
                        position, bar, cash, exit_price_override=exit_price, exit_reason="STOP_LOSS"
                    )
                    trades.append(trade)
                    position = None
                    closed_by_stop_or_target = True
                elif target_hit:
                    # A profit target behaves like a limit order: filled at
                    # the target price, or better (bar.open) if the bar
                    # gapped up through it overnight, then subject to the
                    # same execution slippage as any other exit.
                    trigger_price = max(bar.open, position.target_price)
                    exit_price = trigger_price * (Decimal("1") - self.config.slippage_rate)
                    trade, cash = self._close_position(
                        position, bar, cash, exit_price_override=exit_price, exit_reason="TARGET"
                    )
                    trades.append(trade)
                    position = None
                    closed_by_stop_or_target = True

            if not closed_by_stop_or_target and signal and signal.action == "SELL" and position is not None:
                trade, cash = self._close_position(position, bar, cash, exit_reason="SIGNAL")
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
            trade, cash = self._close_position(position, bars[-1], cash, at_open=False, exit_reason="FORCED_LIQUIDATION")
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
        exit_price_override: Decimal | None = None,
        exit_reason: str = "SIGNAL",
    ) -> tuple[Trade, Decimal]:
        """Close a position and realize its trade.

        ``at_open=True`` (the normal, signal-driven exit) prices at this
        bar's open with slippage, matching next-bar-open execution timing.
        ``at_open=False`` is used only for a forced end-of-backtest
        liquidation, pricing at this bar's close with the same slippage —
        never at an unrealistically slippage-free close, and never mixing
        entry/exit timing bases within one trade.

        ``exit_price_override`` is used for stop-loss/target exits, which are
        priced against the position's own stop/target level (already the
        worse-of/better-of the trigger and this bar's open — see ``run()``),
        not against ``bar.open``/``bar.close`` with an additional slippage
        adjustment layered on top.
        """

        if exit_price_override is not None:
            exit_price = exit_price_override
        else:
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
            exit_reason=exit_reason,
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
