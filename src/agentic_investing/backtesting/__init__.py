"""Deterministic historical backtesting components."""

from .engine import BacktestConfig, BacktestResult, Backtester, Trade
from .evaluation import (
    CostScenario,
    CostSensitivityResult,
    ValidationReport,
    WalkForwardRun,
    WalkForwardWindow,
    chronological_split,
    evaluate_train_test,
    evaluate_walk_forward,
    generate_walk_forward_windows,
    render_validation_report,
    run_buy_and_hold,
    run_cash_benchmark,
    run_cost_sensitivity,
)
from .metrics import PerformanceMetrics, calculate_metrics
from .selection import StrategyCandidate, StrategyScore, StrategySelection, compare_strategies

__all__ = [
    "BacktestConfig",
    "BacktestResult",
    "Backtester",
    "CostScenario",
    "CostSensitivityResult",
    "PerformanceMetrics",
    "StrategyCandidate",
    "StrategyScore",
    "StrategySelection",
    "Trade",
    "ValidationReport",
    "WalkForwardRun",
    "WalkForwardWindow",
    "calculate_metrics",
    "chronological_split",
    "evaluate_train_test",
    "evaluate_walk_forward",
    "generate_walk_forward_windows",
    "render_validation_report",
    "run_buy_and_hold",
    "run_cash_benchmark",
    "run_cost_sensitivity",
    "compare_strategies",
]
