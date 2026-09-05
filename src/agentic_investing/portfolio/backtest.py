"""Multi-instrument portfolio backtest for deterministic Stage 2 research."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence

from agentic_investing.backtesting.metrics import PerformanceMetrics, calculate_metrics
from agentic_investing.data.models import Bar
from agentic_investing.execution import OrderManager, PaperBroker
from agentic_investing.risk import RiskEngine, RiskLimits

from .models import FundamentalSnapshot, PortfolioDecision, ScreeningConfig
from .screener import build_portfolio_decisions


@dataclass(frozen=True, slots=True)
class PortfolioBacktestResult:
    initial_capital: Decimal
    final_capital: Decimal
    equity_curve: tuple[Decimal, ...]
    trade_pnls: tuple[Decimal, ...]
    decisions: tuple[PortfolioDecision, ...]
    metrics: PerformanceMetrics


@dataclass(slots=True)
class _Plan:
    stop_price: Decimal
    target_price: Decimal


class PortfolioBacktester:
    """Backtest screening, ranking, allocation, and exits across instruments."""

    def __init__(
        self,
        *,
        initial_capital: Decimal = Decimal("100000"),
        commission_rate: Decimal = Decimal("0.0003"),
        slippage_rate: Decimal = Decimal("0.0005"),
        risk_limits: RiskLimits | None = None,
        screening_config: ScreeningConfig | None = None,
    ) -> None:
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.risk_limits = risk_limits or RiskLimits(account_capital=initial_capital)
        self.screening_config = screening_config or ScreeningConfig(max_positions=self.risk_limits.max_positions)
        if self.risk_limits.account_capital != initial_capital:
            raise ValueError("risk limits capital must match portfolio backtest capital")

    def run(
        self,
        bars_by_instrument: Mapping[str, Sequence[Bar]],
        fundamentals_by_instrument: Mapping[str, FundamentalSnapshot],
    ) -> PortfolioBacktestResult:
        self._validate_bars(bars_by_instrument)
        instrument_names = tuple(bars_by_instrument)
        bar_count = len(next(iter(bars_by_instrument.values())))
        broker = PaperBroker(self.initial_capital, self.commission_rate)
        risk_engine = RiskEngine(self.risk_limits)
        order_manager = OrderManager(broker, risk_engine)
        plans: dict[str, _Plan] = {}
        entry_prices: dict[str, Decimal] = {}
        trade_pnls: list[Decimal] = []
        equity_curve: list[Decimal] = [self.initial_capital]
        decisions: list[PortfolioDecision] = []

        for index in range(bar_count):
            current_bars = {name: bars_by_instrument[name][index] for name in instrument_names}
            timestamp = next(iter(current_bars.values())).timestamp
            equity_before = broker.cash_balance() + sum(
                (position.quantity * current_bars[position.instrument].open for position in broker.list_positions()),
                Decimal("0"),
            )
            risk_engine.mark_to_market(equity_before, timestamp)

            for name, bar in current_bars.items():
                position = next((item for item in broker.list_positions() if item.instrument == name), None)
                plan = plans.get(name)
                if position is not None and plan is not None:
                    if bar.low <= plan.stop_price:
                        pnl = self._sell(
                            order_manager, broker, name, bar, position.quantity,
                            min(bar.open, plan.stop_price) * (Decimal("1") - self.slippage_rate),
                            f"{name}-{bar.timestamp.isoformat()}-portfolio-stop",
                        )
                        trade_pnls.append(pnl)
                        plans.pop(name, None)
                        entry_prices.pop(name, None)
                    elif bar.high >= plan.target_price:
                        pnl = self._sell(
                            order_manager, broker, name, bar, position.quantity,
                            max(bar.open, plan.target_price) * (Decimal("1") - self.slippage_rate),
                            f"{name}-{bar.timestamp.isoformat()}-portfolio-target",
                        )
                        trade_pnls.append(pnl)
                        plans.pop(name, None)
                        entry_prices.pop(name, None)

            if index > 0:
                holdings = {position.instrument for position in broker.list_positions()}
                current_decisions = build_portfolio_decisions(
                    bars_by_instrument,
                    fundamentals_by_instrument,
                    index - 1,
                    holdings=holdings,
                    config=self.screening_config,
                )
                decisions.extend(current_decisions)
                self._execute_decisions(
                    current_decisions,
                    current_bars,
                    broker,
                    order_manager,
                    plans,
                    entry_prices,
                    trade_pnls,
                    risk_engine,
                    timestamp,
                )

            equity_curve.append(
                broker.cash_balance()
                + sum(
                    (position.quantity * current_bars[position.instrument].close for position in broker.list_positions()),
                    Decimal("0"),
                )
            )

        final_timestamp = next(iter(bars_by_instrument.values()))[-1].timestamp
        for position in list(broker.list_positions()):
            bar = bars_by_instrument[position.instrument][-1]
            trade_pnls.append(
                self._sell(
                    order_manager,
                    broker,
                    position.instrument,
                    bar,
                    position.quantity,
                    bar.close * (Decimal("1") - self.slippage_rate),
                    f"{position.instrument}-{final_timestamp.isoformat()}-portfolio-final",
                )
            )
        equity_curve[-1] = broker.cash_balance()
        metrics = calculate_metrics(
            initial_capital=self.initial_capital,
            final_capital=broker.cash_balance(),
            equity_curve=equity_curve,
            trade_pnls=trade_pnls,
        )
        return PortfolioBacktestResult(
            self.initial_capital,
            broker.cash_balance(),
            tuple(equity_curve),
            tuple(trade_pnls),
            tuple(decisions),
            metrics,
        )

    def _execute_decisions(
        self,
        decisions: Sequence[PortfolioDecision],
        bars: Mapping[str, Bar],
        broker: PaperBroker,
        order_manager: OrderManager,
        plans: dict[str, _Plan],
        entry_prices: dict[str, Decimal],
        trade_pnls: list[Decimal],
        risk_engine: RiskEngine,
        timestamp: datetime,
    ) -> None:
        for decision in decisions:
            position = next((item for item in broker.list_positions() if item.instrument == decision.instrument), None)
            bar = bars[decision.instrument]
            if decision.action == "SELL" and position is not None:
                trade_pnls.append(
                    self._sell(
                        order_manager, broker, decision.instrument, bar, position.quantity,
                        bar.open * (Decimal("1") - self.slippage_rate),
                        f"{decision.instrument}-{timestamp.isoformat()}-portfolio-sell",
                    )
                )
                plans.pop(decision.instrument, None)
                entry_prices.pop(decision.instrument, None)
            elif decision.action == "BUY" and position is None:
                outcome = order_manager.submit_buy(
                    client_order_id=f"{decision.instrument}-{timestamp.isoformat()}-portfolio-buy",
                    instrument=decision.instrument,
                    exchange=decision.exchange,
                    equity=broker.cash_balance(),
                    fill_price=bar.open * (Decimal("1") + self.slippage_rate),
                    stop_distance_fraction=Decimal("0.05"),
                    initial_capital=self.initial_capital,
                    commission_rate=self.commission_rate,
                    timestamp=timestamp,
                )
                if outcome.submitted and decision.candidate is not None:
                    fill = outcome.order.average_fill_price if outcome.order is not None else None
                    if fill is not None:
                        entry_prices[decision.instrument] = fill
                        plans[decision.instrument] = _Plan(
                            stop_price=decision.candidate.stop_price,
                            target_price=decision.candidate.target_price,
                        )

    @staticmethod
    def _sell(
        order_manager: OrderManager,
        broker: PaperBroker,
        instrument: str,
        bar: Bar,
        quantity: int,
        fill_price: Decimal,
        client_order_id: str,
    ) -> Decimal:
        position = next(item for item in broker.list_positions() if item.instrument == instrument)
        outcome = order_manager.submit_sell(
            client_order_id=client_order_id,
            instrument=instrument,
            exchange=bar.exchange,
            quantity=quantity,
            fill_price=fill_price,
            timestamp=bar.timestamp,
        )
        if not outcome.submitted:
            return Decimal("0")
        return (fill_price - position.average_price) * quantity

    @staticmethod
    def _validate_bars(bars_by_instrument: Mapping[str, Sequence[Bar]]) -> None:
        if not bars_by_instrument:
            raise ValueError("at least one instrument is required")
        lengths = {len(bars) for bars in bars_by_instrument.values()}
        if 0 in lengths or len(lengths) != 1:
            raise ValueError("all instruments require the same non-empty bar count")
        first_timestamps = {tuple(bar.timestamp for bar in bars) for bars in bars_by_instrument.values()}
        if len(first_timestamps) != 1:
            raise ValueError("all instruments must share aligned timestamps")
