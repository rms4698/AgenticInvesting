"""Technical-only multi-instrument portfolio backtesting."""

from __future__ import annotations

from bisect import bisect_left
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo

from agentic_investing.backtesting.metrics import PerformanceMetrics, calculate_metrics
from agentic_investing.data.models import Bar
from agentic_investing.execution import OrderManager, PaperBroker
from agentic_investing.features import TechnicalSnapshot, calculate_technical_snapshot
from agentic_investing.risk import RiskEngine, RiskLimits

INDIA = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True, slots=True)
class TechnicalOnlyConfig:
    """Explicit technical rules for daily positional research."""

    fast_period: int = 20
    slow_period: int = 50
    rsi_period: int = 14
    atr_period: int = 14
    volume_period: int = 20
    minimum_rsi: Decimal = Decimal("50")
    maximum_rsi: Decimal = Decimal("70")
    minimum_volume_ratio: Decimal = Decimal("1")
    stop_atr_multiple: Decimal = Decimal("2")
    minimum_reward_risk: Decimal = Decimal("1.5")
    use_profit_target: bool = True
    trailing_stop_atr_multiple: Decimal | None = None
    max_positions: int = 8
    universe_size: int = 200
    universe_rebalance_days: int = 21
    liquidity_window: int = 20
    relative_strength_periods: tuple[int, ...] = (63, 126, 252)
    minimum_relative_strength: Decimal = Decimal("0")
    weekly_sma_period: int = 30
    weekly_slope_lookback: int = 4
    minimum_close_to_52_week_high: Decimal = Decimal("0.90")
    volatility_contraction_lookback: int = 20
    require_weekly_confirmation: bool = False
    require_relative_strength: bool = False
    require_52_week_proximity: bool = False
    breakout_lookback: int = 120
    require_breakout: bool = False
    breakout_volume_ratio: Decimal = Decimal("1")
    enable_pyramiding: bool = False
    max_pyramid_additions: int = 2
    pyramid_trigger_atr_multiple: Decimal = Decimal("1")
    pyramid_quantity_fraction: Decimal = Decimal("0.5")
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        periods = (self.fast_period, self.slow_period, self.rsi_period, self.atr_period, self.volume_period)
        if min(periods) < 1 or self.slow_period <= self.fast_period:
            raise ValueError("technical lookback periods are invalid")
        if not Decimal("0") <= self.minimum_rsi <= self.maximum_rsi <= Decimal("100"):
            raise ValueError("RSI bounds must be between 0 and 100")
        if self.minimum_volume_ratio < 0 or self.stop_atr_multiple <= 0 or self.minimum_reward_risk < 1:
            raise ValueError("technical thresholds are invalid")
        if self.trailing_stop_atr_multiple is not None and self.trailing_stop_atr_multiple <= 0:
            raise ValueError("trailing_stop_atr_multiple must be positive")
        if not self.use_profit_target and self.trailing_stop_atr_multiple is None:
            raise ValueError("a trailing stop is required when profit target exits are disabled")
        if self.max_positions < 1:
            raise ValueError("max_positions must be positive")
        if self.universe_size < 1 or self.universe_rebalance_days < 1 or self.liquidity_window < 1:
            raise ValueError("universe settings must be positive")
        if not self.relative_strength_periods or min(self.relative_strength_periods) < 1:
            raise ValueError("relative strength periods must be positive")
        if self.minimum_close_to_52_week_high <= 0 or self.minimum_close_to_52_week_high > 1:
            raise ValueError("minimum_close_to_52_week_high must be in (0, 1]")
        if self.weekly_sma_period < 2 or self.weekly_slope_lookback < 1:
            raise ValueError("weekly settings are invalid")
        if self.breakout_lookback < self.slow_period or self.breakout_volume_ratio < 0:
            raise ValueError("breakout settings are invalid")
        if self.max_pyramid_additions < 0 or self.pyramid_trigger_atr_multiple <= 0:
            raise ValueError("pyramiding settings are invalid")
        if not Decimal("0") < self.pyramid_quantity_fraction <= Decimal("1"):
            raise ValueError("pyramid_quantity_fraction must be in (0, 1]")
        if self.start is not None and self.start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        if self.end is not None and self.end.tzinfo is None:
            raise ValueError("end must be timezone-aware")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("start must not be after end")


@dataclass(frozen=True, slots=True)
class TechnicalOnlyCandidate:
    instrument: str
    exchange: str
    score: Decimal
    technical: TechnicalSnapshot
    stop_price: Decimal
    target_price: Decimal | None
    relative_strength: Decimal


@dataclass(frozen=True, slots=True)
class AllocationSnapshot:
    timestamp: datetime
    position_count: int
    deployed_capital: Decimal
    deployment_fraction: Decimal
    instruments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TradeRecord:
    instrument: str
    exchange: str
    entry_time: datetime
    exit_time: datetime
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    pnl: Decimal
    exit_reason: str


@dataclass(frozen=True, slots=True)
class TechnicalOnlyBacktestResult:
    initial_capital: Decimal
    final_capital: Decimal
    start: datetime
    end: datetime
    candidate_count: int
    equity_curve: tuple[Decimal, ...]
    trade_pnls: tuple[Decimal, ...]
    allocation_history: tuple[AllocationSnapshot, ...]
    trade_records: tuple[TradeRecord, ...]
    metrics: PerformanceMetrics
    cagr: Decimal
    max_positions_held: int
    average_deployment_fraction: Decimal
    kill_switch_triggered: bool
    kill_switch_reason: str | None


@dataclass(frozen=True, slots=True)
class _PendingAction:
    action: str
    candidate: TechnicalOnlyCandidate | None = None


@dataclass(slots=True)
class _Plan:
    stop_price: Decimal
    target_price: Decimal | None
    highest_close: Decimal
    trailing_multiple: Decimal | None
    pyramid_additions: int = 0


@dataclass(slots=True)
class _IndicatorState:
    last_length: int = 0
    previous_close: Decimal | None = None
    fast_values: deque[Decimal] = field(default_factory=deque)
    slow_values: deque[Decimal] = field(default_factory=deque)
    volume_values: deque[Decimal] = field(default_factory=deque)
    gains: deque[Decimal] = field(default_factory=deque)
    losses: deque[Decimal] = field(default_factory=deque)
    true_ranges: deque[Decimal] = field(default_factory=deque)
    fast_sum: Decimal = Decimal("0")
    slow_sum: Decimal = Decimal("0")
    volume_sum: Decimal = Decimal("0")
    gain_sum: Decimal = Decimal("0")
    loss_sum: Decimal = Decimal("0")
    true_range_sum: Decimal = Decimal("0")


class TechnicalOnlyBacktester:
    """Backtest technical selection over an outer calendar of unequal histories."""

    def __init__(
        self,
        *,
        config: TechnicalOnlyConfig | None = None,
        initial_capital: Decimal = Decimal("100000"),
        commission_rate: Decimal = Decimal("0.0003"),
        slippage_rate: Decimal = Decimal("0.0005"),
        risk_limits: RiskLimits | None = None,
        market_regime_bars: Sequence[Bar] | None = None,
        market_fast_period: int = 50,
        market_slow_period: int = 200,
    ) -> None:
        self.config = config or TechnicalOnlyConfig()
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage_rate = slippage_rate
        self.market_regime_bars = (
            tuple(sorted(market_regime_bars, key=lambda bar: bar.timestamp))
            if market_regime_bars is not None
            else None
        )
        if market_fast_period < 1 or market_slow_period <= market_fast_period:
            raise ValueError("market regime periods are invalid")
        self.market_fast_period = market_fast_period
        self.market_slow_period = market_slow_period
        self._weekly_cache: dict[str, tuple[tuple[int, int], bool | None]] = {}
        self._snapshot_cache: dict[tuple[str, datetime], TechnicalSnapshot | None] = {}
        self._indicator_states: dict[str, _IndicatorState] = {}
        self.risk_limits = risk_limits or RiskLimits(
            account_capital=initial_capital,
            max_positions=self.config.max_positions,
        )
        if self.risk_limits.account_capital != initial_capital:
            raise ValueError("risk limits capital must match initial capital")

    def run(self, bars_by_instrument: Mapping[str, Sequence[Bar]]) -> TechnicalOnlyBacktestResult:
        self._weekly_cache.clear()
        self._snapshot_cache.clear()
        self._indicator_states.clear()
        normalized = self._validate_and_normalize(bars_by_instrument)
        start = self.config.start or min(bar.timestamp for bars in normalized.values() for bar in bars)
        end = self.config.end or max(bar.timestamp for bars in normalized.values() for bar in bars)
        event_times = sorted(
            {
                bar.timestamp
                for bars in normalized.values()
                for bar in bars
                if start <= bar.timestamp <= end
            }
        )
        if not event_times:
            raise ValueError("no bars fall inside the configured backtest window")

        broker = PaperBroker(self.initial_capital, self.commission_rate)
        risk_engine = RiskEngine(self.risk_limits)
        order_manager = OrderManager(broker, risk_engine)
        histories: dict[str, list[Bar]] = {
            instrument: [bar for bar in bars if bar.timestamp < start]
            for instrument, bars in normalized.items()
        }
        events_by_timestamp: dict[datetime, list[tuple[str, Bar]]] = {}
        for instrument, bars in normalized.items():
            for bar in bars:
                if start <= bar.timestamp <= end:
                    events_by_timestamp.setdefault(bar.timestamp, []).append((instrument, bar))
        self._weekly_series = {
            instrument: self._build_weekly_series(bars) for instrument, bars in normalized.items()
        }
        regime_history: list[Bar] = [
            bar for bar in (self.market_regime_bars or ()) if bar.timestamp < start
        ]
        regime_events: dict[datetime, list[Bar]] = {}
        for bar in self.market_regime_bars or ():
            if start <= bar.timestamp <= end:
                regime_events.setdefault(bar.timestamp, []).append(bar)
        pending: dict[str, _PendingAction] = {}
        plans: dict[str, _Plan] = {}
        trade_pnls: list[Decimal] = []
        equity_curve: list[Decimal] = [self.initial_capital]
        allocation_history: list[AllocationSnapshot] = []
        trade_records: list[TradeRecord] = []
        active_entries: dict[str, list[tuple[datetime, Decimal, int]]] = {}
        active_universe: set[str] = set()
        last_rebalance_index = -self.config.universe_rebalance_days

        for timestamp in event_times:
            regime_history.extend(regime_events.get(timestamp, ()))
            current_bars: dict[str, Bar] = {}
            for instrument, bar in events_by_timestamp.get(timestamp, ()):
                histories[instrument].append(bar)
                current_bars[instrument] = bar

            equity_before = self._equity_at_prices(broker, histories, use_open=True)
            risk_engine.mark_to_market(equity_before, timestamp)
            self._execute_pending(
                pending,
                current_bars,
                histories,
                broker,
                order_manager,
                plans,
                trade_pnls,
                trade_records,
                active_entries,
                timestamp,
            )
            self._execute_stops_and_targets(
                current_bars,
                histories,
                broker,
                order_manager,
                plans,
                trade_pnls,
                trade_records,
                active_entries,
                timestamp,
            )
            if self.config.enable_pyramiding:
                self._schedule_pyramids(current_bars, histories, broker, plans, pending)

            event_index = len(equity_curve) - 1
            if event_index - last_rebalance_index >= self.config.universe_rebalance_days:
                active_universe = self._rank_active_universe(histories, timestamp)
                last_rebalance_index = event_index

            candidates: dict[str, TechnicalOnlyCandidate] = {}
            regime_allows_entries = self._market_regime_allows_entries(regime_history)
            for instrument, bar in current_bars.items():
                position = self._position(broker, instrument, bar.exchange)
                if position is None and instrument not in active_universe:
                    continue
                snapshot = self._technical_snapshot(histories[instrument])
                if snapshot is None:
                    continue
                if position is not None:
                    if snapshot.sma_fast < snapshot.sma_slow:
                        pending[instrument] = _PendingAction("SELL")
                    continue
                candidate = (
                    self._candidate(snapshot, histories[instrument], regime_history)
                    if regime_allows_entries and instrument in active_universe
                    else None
                )
                if candidate is not None:
                    candidates[instrument] = candidate

            open_instruments = {position.instrument for position in broker.list_positions()}
            pending_buys = sum(action.action == "BUY" for action in pending.values())
            slots = max(0, self.config.max_positions - len(open_instruments) - pending_buys)
            for candidate in sorted(candidates.values(), key=lambda item: (-item.score, item.instrument))[:slots]:
                pending[candidate.instrument] = _PendingAction("BUY", candidate)

            equity_close = self._equity_at_prices(broker, histories, use_open=False)
            equity_curve.append(equity_close)
            allocation_history.append(self._allocation_snapshot(timestamp, broker, histories, self.initial_capital))

        final_timestamp = event_times[-1]
        for position in list(broker.list_positions()):
            bar = histories[position.instrument][-1]
            trade_pnls.append(
                self._sell(
                    order_manager,
                    broker,
                    bar,
                    position.quantity,
                    bar.close * (Decimal("1") - self.slippage_rate),
                    f"{position.instrument}-{final_timestamp.isoformat()}-technical-final",
                    trade_records=trade_records,
                    active_entries=active_entries,
                    exit_reason="final",
                )
            )
        equity_curve[-1] = broker.cash_balance()
        metrics = calculate_metrics(
            initial_capital=self.initial_capital,
            final_capital=broker.cash_balance(),
            equity_curve=equity_curve,
            trade_pnls=trade_pnls,
        )
        years = Decimal(str(max((end - start).total_seconds() / 31_557_600, 1 / 365.25)))
        cagr = (broker.cash_balance() / self.initial_capital) ** (Decimal("1") / years) - Decimal("1")
        average_deployment = (
            sum((item.deployment_fraction for item in allocation_history), Decimal("0"))
            / Decimal(len(allocation_history))
            if allocation_history
            else Decimal("0")
        )
        return TechnicalOnlyBacktestResult(
            initial_capital=self.initial_capital,
            final_capital=broker.cash_balance(),
            start=start,
            end=end,
            candidate_count=len(normalized),
            equity_curve=tuple(equity_curve),
            trade_pnls=tuple(trade_pnls),
            allocation_history=tuple(allocation_history),
            trade_records=tuple(trade_records),
            metrics=metrics,
            cagr=cagr,
            max_positions_held=max((item.position_count for item in allocation_history), default=0),
            average_deployment_fraction=average_deployment,
            kill_switch_triggered=risk_engine.kill_switch_triggered,
            kill_switch_reason=risk_engine.kill_switch_reason,
        )

    def _candidate(
        self,
        snapshot: TechnicalSnapshot,
        history: Sequence[Bar],
        benchmark_history: Sequence[Bar],
    ) -> TechnicalOnlyCandidate | None:
        if snapshot.sma_fast <= snapshot.sma_slow:
            return None
        if snapshot.close <= snapshot.sma_slow:
            return None
        if not self.config.minimum_rsi <= snapshot.rsi <= self.config.maximum_rsi:
            return None
        if snapshot.volume_ratio < self.config.minimum_volume_ratio or snapshot.atr <= 0:
            return None
        weekly = self._weekly_confirmation(snapshot.instrument, history)
        if self.config.require_weekly_confirmation and (weekly is None or not weekly):
            return None
        relative_strength = self._relative_strength(history, benchmark_history)
        if relative_strength is None and self.config.require_relative_strength:
            return None
        relative_strength = relative_strength or tuple(Decimal("0") for _ in self.config.relative_strength_periods)
        if self.config.require_relative_strength and min(relative_strength) < self.config.minimum_relative_strength:
            return None
        high_lookback = min(252, len(history))
        close_to_high = snapshot.close / max(bar.close for bar in history[-high_lookback:])
        if self.config.require_52_week_proximity and close_to_high < self.config.minimum_close_to_52_week_high:
            return None
        breakout_available = len(history) > self.config.breakout_lookback
        breakout_high = (
            max(bar.high for bar in history[-self.config.breakout_lookback - 1 : -1])
            if breakout_available
            else None
        )
        breakout = (
            breakout_high is not None
            and snapshot.close > breakout_high
            and snapshot.volume_ratio >= self.config.breakout_volume_ratio
        )
        if self.config.require_breakout and not breakout:
            return None
        stop_price = snapshot.close - snapshot.atr * self.config.stop_atr_multiple
        if stop_price <= 0:
            return None
        target_price = (
            snapshot.close + (snapshot.close - stop_price) * self.config.minimum_reward_risk
            if self.config.use_profit_target
            else None
        )
        trend_score = (snapshot.sma_fast - snapshot.sma_slow) / snapshot.close
        momentum_score = snapshot.rsi / Decimal("100")
        volume_score = min(snapshot.volume_ratio, Decimal("3")) / Decimal("3")
        relative_score = sum(relative_strength, Decimal("0")) / Decimal(len(relative_strength))
        volatility_score = self._volatility_contraction_score(history, snapshot)
        weekly_score = Decimal("0.2") if weekly else Decimal("0")
        proximity_score = close_to_high
        breakout_score = Decimal("0.25") if breakout else Decimal("0")
        return TechnicalOnlyCandidate(
            instrument=snapshot.instrument,
            exchange=snapshot.exchange,
            score=trend_score + momentum_score + volume_score + relative_score + volatility_score + weekly_score + proximity_score + breakout_score,
            technical=snapshot,
            stop_price=stop_price,
            target_price=target_price,
            relative_strength=relative_score,
        )

    def _rank_active_universe(
        self,
        histories: Mapping[str, Sequence[Bar]],
        timestamp: datetime,
    ) -> set[str]:
        ranked: list[tuple[Decimal, str]] = []
        for instrument, bars in histories.items():
            if len(bars) < self.config.liquidity_window:
                continue
            recent = bars[-self.config.liquidity_window:]
            if timestamp - recent[-1].timestamp > timedelta(days=10):
                continue
            traded_value = sum((bar.close * Decimal(bar.volume) for bar in recent), Decimal("0")) / Decimal(len(recent))
            ranked.append((traded_value, instrument))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return {instrument for _, instrument in ranked[: self.config.universe_size]}

    def _weekly_confirmation(self, instrument: str, bars: Sequence[Bar]) -> bool | None:
        current_local = bars[-1].timestamp.astimezone(INDIA).date().isocalendar()
        current_week = (current_local.year, current_local.week)
        cached = self._weekly_cache.get(instrument)
        if cached is not None and cached[0] == current_week:
            return cached[1]
        series = getattr(self, "_weekly_series", {}).get(instrument, ())
        week_keys = [key for key, _ in series]
        cutoff = bisect_left(week_keys, current_week)
        closes = [close for _, close in series[:cutoff]]
        required = self.config.weekly_sma_period + self.config.weekly_slope_lookback
        if len(closes) < required:
            self._weekly_cache[instrument] = (current_week, None)
            return None
        current_sma = sum(closes[-self.config.weekly_sma_period:], Decimal("0")) / Decimal(self.config.weekly_sma_period)
        prior_end = -(self.config.weekly_sma_period)
        prior_start = prior_end - self.config.weekly_slope_lookback
        prior_sma = sum(closes[prior_start:prior_end], Decimal("0")) / Decimal(self.config.weekly_sma_period)
        result = closes[-1] > current_sma and current_sma > prior_sma
        self._weekly_cache[instrument] = (current_week, result)
        return result

    @staticmethod
    def _build_weekly_series(bars: Sequence[Bar]) -> tuple[tuple[tuple[int, int], Decimal], ...]:
        weekly: dict[tuple[int, int], Decimal] = {}
        for bar in bars:
            local_date = bar.timestamp.astimezone(INDIA).date()
            iso = local_date.isocalendar()
            weekly[(iso.year, iso.week)] = bar.close
        return tuple(sorted(weekly.items()))

    def _relative_strength(
        self,
        bars: Sequence[Bar],
        benchmark_bars: Sequence[Bar],
    ) -> tuple[Decimal, ...] | None:
        if not benchmark_bars:
            return tuple(Decimal("0") for _ in self.config.relative_strength_periods)
        if not benchmark_bars:
            return None
        benchmark = benchmark_bars
        values: list[Decimal] = []
        for period in self.config.relative_strength_periods:
            if len(bars) <= period or len(benchmark) <= period:
                return None
            stock_return = bars[-1].close / bars[-1 - period].close - Decimal("1")
            benchmark_return = benchmark[-1].close / benchmark[-1 - period].close - Decimal("1")
            values.append(stock_return - benchmark_return)
        return tuple(values)

    def _volatility_contraction_score(self, bars: Sequence[Bar], snapshot: TechnicalSnapshot) -> Decimal:
        if len(bars) < self.config.volatility_contraction_lookback * 2:
            return Decimal("0")
        recent = bars[-self.config.volatility_contraction_lookback:]
        prior = bars[-2 * self.config.volatility_contraction_lookback : -self.config.volatility_contraction_lookback]
        recent_range = sum((bar.high - bar.low for bar in recent), Decimal("0")) / Decimal(len(recent))
        prior_range = sum((bar.high - bar.low for bar in prior), Decimal("0")) / Decimal(len(prior))
        return Decimal("0.1") if prior_range > 0 and recent_range < prior_range else Decimal("0")

    def _technical_snapshot(self, bars: Sequence[Bar]) -> TechnicalSnapshot | None:
        cache_key = (bars[-1].instrument, bars[-1].timestamp) if bars else None
        if cache_key is not None and cache_key in self._snapshot_cache:
            return self._snapshot_cache[cache_key]
        instrument = bars[-1].instrument
        state = self._indicator_states.setdefault(instrument, _IndicatorState())
        for bar in bars[state.last_length :]:
            self._update_indicator_state(state, bar)
        state.last_length = len(bars)
        snapshot = self._snapshot_from_state(bars[-1], state)
        if cache_key is not None:
            self._snapshot_cache[cache_key] = snapshot
        return snapshot

    def _update_indicator_state(self, state: _IndicatorState, bar: Bar) -> None:
        state.fast_values.append(bar.close)
        state.fast_sum += bar.close
        if len(state.fast_values) > self.config.fast_period:
            state.fast_sum -= state.fast_values.popleft()
        state.slow_values.append(bar.close)
        state.slow_sum += bar.close
        if len(state.slow_values) > self.config.slow_period:
            state.slow_sum -= state.slow_values.popleft()
        volume = Decimal(bar.volume)
        state.volume_values.append(volume)
        state.volume_sum += volume
        if len(state.volume_values) > self.config.volume_period:
            state.volume_sum -= state.volume_values.popleft()
        if state.previous_close is not None:
            change = bar.close - state.previous_close
            gain = max(change, Decimal("0"))
            loss = max(-change, Decimal("0"))
            state.gains.append(gain)
            state.gain_sum += gain
            state.losses.append(loss)
            state.loss_sum += loss
            if len(state.gains) > self.config.rsi_period:
                state.gain_sum -= state.gains.popleft()
                state.loss_sum -= state.losses.popleft()
            true_range = max(
                bar.high - bar.low,
                abs(bar.high - state.previous_close),
                abs(bar.low - state.previous_close),
            )
            state.true_ranges.append(true_range)
            state.true_range_sum += true_range
            if len(state.true_ranges) > self.config.atr_period:
                state.true_range_sum -= state.true_ranges.popleft()
        state.previous_close = bar.close

    def _snapshot_from_state(self, bar: Bar, state: _IndicatorState) -> TechnicalSnapshot | None:
        if (
            len(state.slow_values) < self.config.slow_period
            or len(state.gains) < self.config.rsi_period
            or len(state.true_ranges) < self.config.atr_period
            or len(state.volume_values) < self.config.volume_period
        ):
            return None
        average_gain = state.gain_sum / Decimal(self.config.rsi_period)
        average_loss = state.loss_sum / Decimal(self.config.rsi_period)
        if average_loss == 0:
            rsi = Decimal("100") if average_gain > 0 else Decimal("50")
        else:
            rsi = Decimal("100") - Decimal("100") / (Decimal("1") + average_gain / average_loss)
        average_volume = state.volume_sum / Decimal(self.config.volume_period)
        return TechnicalSnapshot(
            instrument=bar.instrument,
            exchange=bar.exchange,
            timestamp=bar.timestamp,
            close=bar.close,
            sma_fast=state.fast_sum / Decimal(self.config.fast_period),
            sma_slow=state.slow_sum / Decimal(self.config.slow_period),
            rsi=rsi,
            atr=state.true_range_sum / Decimal(self.config.atr_period),
            volume_ratio=Decimal(bar.volume) / average_volume if average_volume else Decimal("0"),
        )

    def _market_regime_allows_entries(self, bars: Sequence[Bar]) -> bool:
        if self.market_regime_bars is None:
            return True
        if len(bars) < self.market_slow_period:
            return False
        visible = bars[-self.market_slow_period:]
        closes = [bar.close for bar in visible]
        fast = sum(closes[-self.market_fast_period:], Decimal("0")) / Decimal(self.market_fast_period)
        slow = sum(closes, Decimal("0")) / Decimal(self.market_slow_period)
        return visible[-1].close > slow and fast > slow

    def _execute_pending(
        self,
        pending: dict[str, _PendingAction],
        current_bars: Mapping[str, Bar],
        histories: Mapping[str, Sequence[Bar]],
        broker: PaperBroker,
        order_manager: OrderManager,
        plans: dict[str, _Plan],
        trade_pnls: list[Decimal],
        trade_records: list[TradeRecord],
        active_entries: dict[str, list[tuple[datetime, Decimal, int]]],
        timestamp: datetime,
    ) -> None:
        for instrument in tuple(pending):
            bar = current_bars.get(instrument)
            if bar is None:
                continue
            action = pending.pop(instrument)
            position = self._position(broker, instrument, bar.exchange)
            if action.action == "SELL" and position is not None:
                trade_pnls.append(
                    self._sell(
                        order_manager,
                        broker,
                        bar,
                        position.quantity,
                        bar.open * (Decimal("1") - self.slippage_rate),
                        f"{instrument}-{timestamp.isoformat()}-technical-sell",
                        trade_records=trade_records,
                        active_entries=active_entries,
                        exit_reason="trend",
                    )
                )
                plans.pop(instrument, None)
            elif action.action == "PYRAMID" and position is not None:
                plan = plans.get(instrument)
                if plan is None:
                    continue
                max_quantity = max(1, int(position.quantity * self.config.pyramid_quantity_fraction))
                fill_price = bar.open * (Decimal("1") + self.slippage_rate)
                outcome = order_manager.submit_buy(
                    client_order_id=f"{instrument}-{timestamp.isoformat()}-technical-pyramid",
                    instrument=instrument,
                    exchange=bar.exchange,
                    equity=self._equity_at_prices(broker, histories, use_open=True),
                    fill_price=fill_price,
                    stop_distance_fraction=max(
                        (plan.highest_close - plan.stop_price) / plan.highest_close,
                        Decimal("0.01"),
                    ),
                    initial_capital=self.initial_capital,
                    commission_rate=self.commission_rate,
                    timestamp=timestamp,
                    max_quantity=max_quantity,
                )
                if outcome.submitted:
                    plan.pyramid_additions += 1
            elif action.action == "BUY" and position is None and action.candidate is not None:
                stop_distance = (action.candidate.technical.close - action.candidate.stop_price) / action.candidate.technical.close
                fill_price = bar.open * (Decimal("1") + self.slippage_rate)
                outcome = order_manager.submit_buy(
                    client_order_id=f"{instrument}-{timestamp.isoformat()}-technical-buy",
                    instrument=instrument,
                    exchange=bar.exchange,
                    equity=self._equity_at_prices(broker, histories, use_open=True),
                    fill_price=fill_price,
                    stop_distance_fraction=stop_distance,
                    initial_capital=self.initial_capital,
                    commission_rate=self.commission_rate,
                    timestamp=timestamp,
                )
                if outcome.submitted:
                    plans[instrument] = _Plan(
                        stop_price=action.candidate.stop_price,
                        target_price=action.candidate.target_price,
                        highest_close=action.candidate.technical.close,
                        trailing_multiple=self.config.trailing_stop_atr_multiple,
                    )
                    order = outcome.order
                    if order is not None and order.average_fill_price is not None:
                        active_entries.setdefault(instrument, []).append(
                            (
                            timestamp,
                            order.average_fill_price,
                            order.filled_quantity,
                            )
                        )

    def _schedule_pyramids(
        self,
        current_bars: Mapping[str, Bar],
        histories: Mapping[str, Sequence[Bar]],
        broker: PaperBroker,
        plans: Mapping[str, _Plan],
        pending: dict[str, _PendingAction],
    ) -> None:
        for position in broker.list_positions():
            if position.instrument in pending:
                continue
            plan = plans.get(position.instrument)
            bar = current_bars.get(position.instrument)
            history = histories.get(position.instrument)
            if plan is None or bar is None or history is None:
                continue
            if plan.pyramid_additions >= self.config.max_pyramid_additions:
                continue
            snapshot = self._technical_snapshot(history)
            if snapshot is None or snapshot.sma_fast <= snapshot.sma_slow:
                continue
            if bar.close < position.average_price + snapshot.atr * self.config.pyramid_trigger_atr_multiple:
                continue
            pending[position.instrument] = _PendingAction("PYRAMID")

    def _execute_stops_and_targets(
        self,
        current_bars: Mapping[str, Bar],
        histories: Mapping[str, Sequence[Bar]],
        broker: PaperBroker,
        order_manager: OrderManager,
        plans: dict[str, _Plan],
        trade_pnls: list[Decimal],
        trade_records: list[TradeRecord],
        active_entries: dict[str, list[tuple[datetime, Decimal, int]]],
        timestamp: datetime,
    ) -> None:
        for position in list(broker.list_positions()):
            bar = current_bars.get(position.instrument)
            plan = plans.get(position.instrument)
            if bar is None or plan is None:
                continue
            stop_price = plan.stop_price
            current_snapshot = self._technical_snapshot(histories[position.instrument])
            if plan.trailing_multiple is not None and current_snapshot is not None:
                trailing_stop = plan.highest_close - current_snapshot.atr * plan.trailing_multiple
                stop_price = max(stop_price, trailing_stop)
            if bar.low <= stop_price:
                trade_pnls.append(
                    self._sell(
                        order_manager,
                        broker,
                        bar,
                        position.quantity,
                        min(bar.open, stop_price) * (Decimal("1") - self.slippage_rate),
                        f"{position.instrument}-{timestamp.isoformat()}-technical-stop",
                        trade_records=trade_records,
                        active_entries=active_entries,
                        exit_reason="stop",
                    )
                )
                plans.pop(position.instrument, None)
            elif plan.target_price is not None and bar.high >= plan.target_price:
                trade_pnls.append(
                    self._sell(
                        order_manager,
                        broker,
                        bar,
                        position.quantity,
                        max(bar.open, plan.target_price) * (Decimal("1") - self.slippage_rate),
                        f"{position.instrument}-{timestamp.isoformat()}-technical-target",
                        trade_records=trade_records,
                        active_entries=active_entries,
                        exit_reason="target",
                    )
                )
                plans.pop(position.instrument, None)
            elif plan.trailing_multiple is not None:
                plan.highest_close = max(plan.highest_close, bar.close)

    @staticmethod
    def _position(broker: PaperBroker, instrument: str, exchange: str):
        return next(
            (position for position in broker.list_positions() if position.instrument == instrument and position.exchange == exchange),
            None,
        )

    def _equity_at_prices(
        self,
        broker: PaperBroker,
        histories: Mapping[str, Sequence[Bar]],
        *,
        use_open: bool,
    ) -> Decimal:
        equity = broker.cash_balance()
        for position in broker.list_positions():
            history = histories.get(position.instrument, ())
            if not history:
                continue
            bar = history[-1]
            price = bar.open if use_open else bar.close
            equity += position.quantity * price
        return equity

    @staticmethod
    def _allocation_snapshot(
        timestamp: datetime,
        broker: PaperBroker,
        histories: Mapping[str, Sequence[Bar]],
        initial_capital: Decimal,
    ) -> AllocationSnapshot:
        deployed = Decimal("0")
        instruments: list[str] = []
        for position in broker.list_positions():
            history = histories.get(position.instrument, ())
            if history:
                deployed += position.quantity * history[-1].close
            instruments.append(position.instrument)
        return AllocationSnapshot(
            timestamp=timestamp,
            position_count=len(instruments),
            deployed_capital=deployed,
            deployment_fraction=deployed / initial_capital,
            instruments=tuple(sorted(instruments)),
        )

    @staticmethod
    def _sell(
        order_manager: OrderManager,
        broker: PaperBroker,
        bar: Bar,
        quantity: int,
        fill_price: Decimal,
        client_order_id: str,
        *,
        trade_records: list[TradeRecord],
        active_entries: dict[str, list[tuple[datetime, Decimal, int]]],
        exit_reason: str,
    ) -> Decimal:
        position = next(item for item in broker.list_positions() if item.instrument == bar.instrument)
        outcome = order_manager.submit_sell(
            client_order_id=client_order_id,
            instrument=bar.instrument,
            exchange=bar.exchange,
            quantity=quantity,
            fill_price=fill_price,
            timestamp=bar.timestamp,
        )
        if not outcome.submitted:
            return Decimal("0")
        pnl = (fill_price - position.average_price) * quantity
        entries = active_entries.pop(bar.instrument, [])
        for entry_time, entry_price, entry_quantity in entries:
            trade_records.append(
                TradeRecord(
                    instrument=bar.instrument,
                    exchange=bar.exchange,
                    entry_time=entry_time,
                    exit_time=bar.timestamp,
                    entry_price=entry_price,
                    exit_price=fill_price,
                    quantity=min(quantity, entry_quantity),
                    pnl=(fill_price - entry_price) * entry_quantity,
                    exit_reason=exit_reason,
                )
            )
        return pnl

    @staticmethod
    def _validate_and_normalize(
        bars_by_instrument: Mapping[str, Sequence[Bar]],
    ) -> dict[str, list[Bar]]:
        if not bars_by_instrument:
            raise ValueError("at least one instrument is required")
        normalized: dict[str, list[Bar]] = {}
        for instrument, bars in bars_by_instrument.items():
            if not bars:
                continue
            ordered = sorted(bars, key=lambda bar: bar.timestamp)
            timestamps = [bar.timestamp for bar in ordered]
            if len(timestamps) != len(set(timestamps)):
                raise ValueError(f"duplicate timestamps for {instrument}")
            normalized[instrument] = ordered
        if not normalized:
            raise ValueError("at least one non-empty instrument is required")
        return normalized
