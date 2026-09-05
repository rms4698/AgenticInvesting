"""Out-of-sample strategy comparison and risk-aware selection."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Sequence

from agentic_investing.data.models import Bar
from agentic_investing.strategies import TradingStrategy

from .engine import BacktestConfig
from .evaluation import evaluate_walk_forward


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    """Named factory for a deterministic strategy configuration."""

    name: str
    factory: Callable[[], TradingStrategy]


@dataclass(frozen=True, slots=True)
class StrategyScore:
    """Aggregated out-of-sample evidence for one strategy candidate."""

    name: str
    average_test_return: Decimal
    average_test_drawdown: Decimal
    average_sharpe: Decimal
    positive_test_windows: int
    test_windows: int
    risk_adjusted_score: Decimal
    eligible: bool


@dataclass(frozen=True, slots=True)
class StrategySelection:
    """The selected candidate and the full comparison table."""

    selected_name: str | None
    scores: tuple[StrategyScore, ...]


def compare_strategies(
    bars: Sequence[Bar],
    candidates: Sequence[StrategyCandidate],
    *,
    train_size: int,
    test_size: int,
    step: int | None = None,
    config: BacktestConfig | None = None,
    max_average_drawdown: Decimal = Decimal("0.12"),
    min_positive_windows: int = 1,
) -> StrategySelection:
    """Compare candidates using rolling out-of-sample windows.

    Selection is deliberately risk-aware and never based on full-sample
    return maximization. A candidate must have positive average test return,
    stay below the drawdown ceiling, and win at least the configured number
    of test windows. Among eligible candidates, the primary score is average
    return divided by average drawdown; average Sharpe and return break ties.
    If no candidate is eligible, selection fails closed with ``selected_name``
    set to ``None``.
    """

    if not candidates:
        raise ValueError("at least one strategy candidate is required")
    if min_positive_windows < 1:
        raise ValueError("min_positive_windows must be positive")
    if max_average_drawdown <= 0 or max_average_drawdown > 1:
        raise ValueError("max_average_drawdown must be between 0 and 1")

    scores: list[StrategyScore] = []
    for candidate in candidates:
        strategy = candidate.factory()
        runs = evaluate_walk_forward(
            bars,
            strategy,
            train_size=train_size,
            test_size=test_size,
            step=step,
            config=config,
        )
        test_metrics = [run.test_result.metrics for run in runs]
        average_return = sum((metric.total_return for metric in test_metrics), Decimal("0")) / Decimal(len(test_metrics))
        average_drawdown = sum((metric.max_drawdown for metric in test_metrics), Decimal("0")) / Decimal(len(test_metrics))
        average_sharpe = sum((metric.sharpe_ratio for metric in test_metrics), Decimal("0")) / Decimal(len(test_metrics))
        positive_windows = sum(1 for metric in test_metrics if metric.total_return > 0)
        eligible = (
            average_return > 0
            and average_drawdown <= max_average_drawdown
            and positive_windows >= min_positive_windows
        )
        risk_adjusted_score = average_return / average_drawdown if average_drawdown > 0 else Decimal("0")
        scores.append(
            StrategyScore(
                name=candidate.name,
                average_test_return=average_return,
                average_test_drawdown=average_drawdown,
                average_sharpe=average_sharpe,
                positive_test_windows=positive_windows,
                test_windows=len(test_metrics),
                risk_adjusted_score=risk_adjusted_score,
                eligible=eligible,
            )
        )

    eligible_scores = [score for score in scores if score.eligible]
    selected = max(
        eligible_scores,
        key=lambda score: (score.risk_adjusted_score, score.average_sharpe, score.average_test_return),
        default=None,
    )
    return StrategySelection(selected_name=selected.name if selected else None, scores=tuple(scores))
