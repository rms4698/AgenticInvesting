"""Chronological validation, benchmarks, and cost-sensitivity evaluation."""

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Sequence

from agentic_investing.data.models import Bar
from agentic_investing.backtesting.engine import Trade
from agentic_investing.backtesting.metrics import calculate_metrics
from agentic_investing.strategies import TradingStrategy

from .engine import BacktestConfig, BacktestResult, Backtester


@dataclass(frozen=True, slots=True)
class ChronologicalSplit:
    """A time-ordered train/test split represented by an exclusive index."""

    train: tuple[Bar, ...]
    test: tuple[Bar, ...]
    split_index: int


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    """Half-open train and test ranges into the original bar sequence."""

    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True, slots=True)
class WalkForwardRun:
    window: WalkForwardWindow
    train_result: BacktestResult
    test_result: BacktestResult


@dataclass(frozen=True, slots=True)
class CostScenario:
    """A named commission/slippage assumption for sensitivity analysis."""

    name: str
    commission_rate: Decimal
    slippage_rate: Decimal


@dataclass(frozen=True, slots=True)
class CostSensitivityResult:
    scenario: CostScenario
    result: BacktestResult


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Results from chronological validation and matched benchmark runs."""

    train_result: BacktestResult
    test_result: BacktestResult
    train_buy_and_hold: BacktestResult
    test_buy_and_hold: BacktestResult
    test_cash: BacktestResult
    cost_sensitivity: tuple[CostSensitivityResult, ...]


def _format_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.2f}%"


def render_validation_report(report: ValidationReport) -> str:
    """Render a deterministic Markdown summary for review and archiving."""

    lines = [
        "# Backtest Validation Report",
        "",
        "| Run | Return | Max drawdown | Trades | Sharpe |",
        "|---|---:|---:|---:|---:|",
    ]
    rows = (
        ("Train strategy", report.train_result.metrics),
        ("Test strategy", report.test_result.metrics),
        ("Train buy-and-hold", report.train_buy_and_hold.metrics),
        ("Test buy-and-hold", report.test_buy_and_hold.metrics),
        ("Test cash", report.test_cash.metrics),
    )
    for name, metrics in rows:
        lines.append(
            f"| {name} | {_format_percent(metrics.total_return)} | "
            f"{_format_percent(metrics.max_drawdown)} | {metrics.trade_count} | "
            f"{metrics.sharpe_ratio:.2f} |"
        )
    if report.cost_sensitivity:
        lines.extend(["", "## Cost sensitivity", "", "| Scenario | Return | Max drawdown |", "|---|---:|---:|"])
        for sensitivity in report.cost_sensitivity:
            metrics = sensitivity.result.metrics
            lines.append(
                f"| {sensitivity.scenario.name} | {_format_percent(metrics.total_return)} | "
                f"{_format_percent(metrics.max_drawdown)} |"
            )
    lines.extend(
        [
            "",
            "This report is a research artifact, not a return guarantee or investment recommendation.",
        ]
    )
    return "\n".join(lines) + "\n"


def chronological_split(bars: Sequence[Bar], split: int | float) -> ChronologicalSplit:
    """Split bars chronologically; ``split`` may be an index or fraction."""

    if len(bars) < 2:
        raise ValueError("at least two bars are required")
    if isinstance(split, float):
        if not 0 < split < 1:
            raise ValueError("split fraction must be between 0 and 1")
        split_index = int(len(bars) * split)
    else:
        split_index = split
    if not 0 < split_index < len(bars):
        raise ValueError("split index must be between the first and last bar")
    return ChronologicalSplit(tuple(bars[:split_index]), tuple(bars[split_index:]), split_index)


def generate_walk_forward_windows(
    bar_count: int,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
) -> tuple[WalkForwardWindow, ...]:
    """Generate chronological rolling windows using half-open indexes."""

    if bar_count < 1 or train_size < 1 or test_size < 1:
        raise ValueError("bar_count, train_size, and test_size must be positive")
    actual_step = step if step is not None else test_size
    if actual_step < 1:
        raise ValueError("step must be positive")

    windows: list[WalkForwardWindow] = []
    train_start = 0
    while train_start + train_size + test_size <= bar_count:
        train_end = train_start + train_size
        windows.append(WalkForwardWindow(train_start, train_end, train_end, train_end + test_size))
        train_start += actual_step
    if not windows:
        raise ValueError("bar_count is too small for the requested window sizes")
    return tuple(windows)


def _evaluation_slice(
    bars: Sequence[Bar],
    *,
    context_start: int,
    evaluation_start: int,
    evaluation_end: int,
) -> tuple[tuple[Bar, ...], int]:
    if not 0 <= context_start < evaluation_start < evaluation_end <= len(bars):
        raise ValueError("invalid evaluation boundaries")
    return tuple(bars[context_start:evaluation_end]), evaluation_start - context_start


def evaluate_train_test(
    bars: Sequence[Bar],
    strategy: TradingStrategy,
    *,
    split: int | float,
    config: BacktestConfig | None = None,
    cost_scenarios: Sequence[CostScenario] = (),
) -> ValidationReport:
    """Evaluate a strategy chronologically with matched benchmarks.

    Test evaluation receives the preceding training bars for indicator
    warmup, but orders and returns begin at the first test bar.
    """

    split_result = chronological_split(bars, split)
    backtester = Backtester(config)
    train_result = backtester.run(split_result.train, strategy)
    context_start = max(0, split_result.split_index - strategy.warmup_period + 1)
    test_bars, test_start = _evaluation_slice(
        bars,
        context_start=context_start,
        evaluation_start=split_result.split_index,
        evaluation_end=len(bars),
    )
    test_result = backtester.run(test_bars, strategy, start_index=test_start)
    train_buy_and_hold = run_buy_and_hold(split_result.train, config=config)
    test_buy_and_hold = run_buy_and_hold(test_bars, config=config, start_index=test_start)
    test_cash = run_cash_benchmark(test_bars, config=config, start_index=test_start)
    sensitivity = run_cost_sensitivity(
        test_bars,
        strategy,
        scenarios=cost_scenarios,
        config=config,
        start_index=test_start,
    )
    return ValidationReport(
        train_result=train_result,
        test_result=test_result,
        train_buy_and_hold=train_buy_and_hold,
        test_buy_and_hold=test_buy_and_hold,
        test_cash=test_cash,
        cost_sensitivity=sensitivity,
    )


def evaluate_walk_forward(
    bars: Sequence[Bar],
    strategy: TradingStrategy,
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    config: BacktestConfig | None = None,
) -> tuple[WalkForwardRun, ...]:
    """Run independent rolling train/test evaluations."""

    windows = generate_walk_forward_windows(
        len(bars), train_size=train_size, test_size=test_size, step=step
    )
    runs: list[WalkForwardRun] = []
    for window in windows:
        window_bars = tuple(bars[window.train_start : window.test_end])
        train_result = Backtester(config).run(window_bars[:train_size], strategy)
        test_result = Backtester(config).run(window_bars, strategy, start_index=train_size)
        runs.append(WalkForwardRun(window, train_result, test_result))
    return tuple(runs)


def run_buy_and_hold(
    bars: Sequence[Bar],
    *,
    config: BacktestConfig | None = None,
    start_index: int = 0,
) -> BacktestResult:
    """Run a full-capital buy-and-hold benchmark for the selected period."""

    actual_config = config or BacktestConfig()
    if not bars or not 0 <= start_index < len(bars):
        raise ValueError("bars must be non-empty and start_index must be within bars")
    selected = bars[start_index:]
    first = selected[0]
    last = selected[-1]
    entry_price = first.open * (Decimal("1") + actual_config.slippage_rate)
    quantity = int((actual_config.initial_capital / (entry_price * (Decimal("1") + actual_config.commission_rate))))
    if quantity < 1:
        raise ValueError("initial capital is insufficient for one benchmark share")
    entry_value = entry_price * quantity
    entry_cost = entry_value * actual_config.commission_rate
    exit_price = last.close * (Decimal("1") - actual_config.slippage_rate)
    exit_value = exit_price * quantity
    exit_cost = exit_value * actual_config.commission_rate
    final_capital = actual_config.initial_capital - entry_value - entry_cost + exit_value - exit_cost
    curve = [actual_config.initial_capital]
    for bar in selected:
        curve.append(actual_config.initial_capital - entry_value - entry_cost + bar.close * quantity)
    curve[-1] = final_capital
    trade = _benchmark_trade(first, last, quantity, entry_price, exit_price, entry_cost + exit_cost)
    metrics = _metrics(actual_config, final_capital, curve, [trade.net_pnl])
    return BacktestResult(actual_config.initial_capital, final_capital, tuple(curve), (trade,), metrics)


def run_cash_benchmark(
    bars: Sequence[Bar],
    *,
    config: BacktestConfig | None = None,
    start_index: int = 0,
) -> BacktestResult:
    """Run a no-trade cash benchmark over the selected period."""

    actual_config = config or BacktestConfig()
    if not bars or not 0 <= start_index < len(bars):
        raise ValueError("bars must be non-empty and start_index must be within bars")
    curve = tuple(actual_config.initial_capital for _ in range(len(bars) - start_index + 1))
    metrics = _metrics(actual_config, actual_config.initial_capital, curve, [])
    return BacktestResult(actual_config.initial_capital, actual_config.initial_capital, curve, (), metrics)


def run_cost_sensitivity(
    bars: Sequence[Bar],
    strategy: TradingStrategy,
    *,
    scenarios: Sequence[CostScenario],
    config: BacktestConfig | None = None,
    start_index: int = 0,
) -> tuple[CostSensitivityResult, ...]:
    """Run identical data/strategy through multiple cost assumptions."""

    actual_config = config or BacktestConfig()
    results = []
    for scenario in scenarios:
        scenario_config = replace(
            actual_config,
            commission_rate=scenario.commission_rate,
            slippage_rate=scenario.slippage_rate,
        )
        result = Backtester(scenario_config).run(bars, strategy, start_index=start_index)
        results.append(CostSensitivityResult(scenario, result))
    return tuple(results)


def _benchmark_trade(first: Bar, last: Bar, quantity: int, entry: Decimal, exit: Decimal, costs: Decimal):
    gross = (exit - entry) * quantity
    return Trade(first.instrument, first.timestamp, last.timestamp, quantity, entry, exit, gross, costs, gross - costs)


def _metrics(
    config: BacktestConfig,
    final_capital: Decimal,
    curve: Sequence[Decimal],
    trade_pnls: Sequence[Decimal],
):
    return calculate_metrics(
        initial_capital=config.initial_capital,
        final_capital=final_capital,
        equity_curve=curve,
        trade_pnls=trade_pnls,
        periods_per_year=config.periods_per_year,
    )
