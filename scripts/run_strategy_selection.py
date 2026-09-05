"""Compare deterministic strategies using rolling out-of-sample evidence.

This is research only. It never places orders and never changes the active
strategy automatically. A human reviews the report before choosing a
candidate for paper/shadow mode.
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.backtesting import BacktestConfig, StrategyCandidate, compare_strategies
from agentic_investing.data import load_bars_json
from agentic_investing.logging_config import get_logger
from agentic_investing.strategies import DonchianBreakoutStrategy, SmaCrossoverStrategy

LOGGER = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare deterministic strategies with walk-forward validation")
    parser.add_argument("--dataset", default="data/real/nse_niftybees_1d.json")
    parser.add_argument("--train-size", type=int, default=500)
    parser.add_argument("--test-size", type=int, default=125)
    parser.add_argument("--step", type=int, default=None)
    parser.add_argument("--max-average-drawdown", type=Decimal, default=Decimal("0.12"))
    parser.add_argument("--min-positive-windows", type=int, default=1)
    parser.add_argument("--output", default="reports/strategy_selection/latest.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    dataset = ROOT / args.dataset
    bars = load_bars_json(dataset)
    candidates = (
        StrategyCandidate("sma_20_50", lambda: SmaCrossoverStrategy(fast_period=20, slow_period=50)),
        StrategyCandidate("donchian_20", lambda: DonchianBreakoutStrategy(lookback_period=20)),
    )
    config = BacktestConfig(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_rate=Decimal("0.0005"),
        stop_distance_fraction=Decimal("0.05"),
        stop_loss_distance_fraction=Decimal("0.20"),
    )
    selection = compare_strategies(
        bars,
        candidates,
        train_size=args.train_size,
        test_size=args.test_size,
        step=args.step,
        config=config,
        max_average_drawdown=args.max_average_drawdown,
        min_positive_windows=args.min_positive_windows,
    )

    lines = [
        "# Deterministic Strategy Selection",
        "",
        f"Dataset: `{args.dataset}`",
        f"Bars: {len(bars)}",
        f"Walk-forward train/test: {args.train_size}/{args.test_size}",
        "",
        "| Strategy | Avg test return | Avg test drawdown | Avg Sharpe | Positive windows | Eligible | Risk-adjusted score |",
        "|---|---:|---:|---:|---:|:---:|---:|",
    ]
    for score in selection.scores:
        lines.append(
            f"| {score.name} | {score.average_test_return * 100:.2f}% | "
            f"{score.average_test_drawdown * 100:.2f}% | {score.average_sharpe:.2f} | "
            f"{score.positive_test_windows}/{score.test_windows} | "
            f"{'yes' if score.eligible else 'no'} | {score.risk_adjusted_score:.4f} |"
        )
    lines.extend(
        [
            "",
            f"**Selected candidate:** `{selection.selected_name or 'NONE - HOLD / review required'}`",
            "",
            "Selection is based only on rolling out-of-sample evidence and a drawdown gate. "
            "It does not automatically activate a strategy or place orders.",
        ]
    )
    report = "\n".join(lines) + "\n"
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    LOGGER.info("strategy_selection_complete selected=%s candidates=%d", selection.selected_name, len(selection.scores))
    print(report)
    print(f"Report written to {output}")
    return 0 if selection.selected_name else 1


if __name__ == "__main__":
    raise SystemExit(main())
