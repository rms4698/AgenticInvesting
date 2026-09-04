"""Run chronological validation on a fetched historical dataset.

This script only performs research/backtesting. It never places orders.
"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.backtesting import (
    BacktestConfig,
    CostScenario,
    evaluate_train_test,
    evaluate_walk_forward,
    render_validation_report,
)
from agentic_investing.data import load_bars_json
from agentic_investing.strategies import SmaCrossoverStrategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the baseline strategy on a fetched dataset")
    parser.add_argument("--dataset", required=True, help="Path to a JSON dataset from ingest_historical_bars")
    parser.add_argument("--fast-period", type=int, default=20)
    parser.add_argument("--slow-period", type=int, default=50)
    parser.add_argument("--split", type=float, default=0.7)
    parser.add_argument("--train-size", type=int, default=500)
    parser.add_argument("--test-size", type=int, default=125)
    parser.add_argument("--output", default=None, help="Optional path to write the Markdown report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bars = load_bars_json(args.dataset)
    print(f"Loaded {len(bars)} bars from {args.dataset}")
    print(f"Range: {bars[0].timestamp.date()} to {bars[-1].timestamp.date()}")

    strategy = SmaCrossoverStrategy(fast_period=args.fast_period, slow_period=args.slow_period)
    config = BacktestConfig(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_rate=Decimal("0.0005"),
        stop_distance_fraction=Decimal("0.05"),
    )
    cost_scenarios = (
        CostScenario("base", Decimal("0.0003"), Decimal("0.0005")),
        CostScenario("stressed", Decimal("0.001"), Decimal("0.002")),
    )

    report = evaluate_train_test(
        bars,
        strategy,
        split=args.split,
        config=config,
        cost_scenarios=cost_scenarios,
    )
    rendered = render_validation_report(report)
    print()
    print(rendered)

    print("Running walk-forward evaluation...")
    try:
        walk_forward_runs = evaluate_walk_forward(
            bars,
            strategy,
            train_size=args.train_size,
            test_size=args.test_size,
            config=config,
        )
        print(f"Walk-forward windows: {len(walk_forward_runs)}")
        wf_lines = ["", "## Walk-forward results", "", "| Window | Train return | Test return | Test max DD | Test trades |", "|---|---:|---:|---:|---:|"]
        for index, run in enumerate(walk_forward_runs, start=1):
            train_metrics = run.train_result.metrics
            test_metrics = run.test_result.metrics
            wf_lines.append(
                f"| {index} | {train_metrics.total_return * 100:.2f}% | "
                f"{test_metrics.total_return * 100:.2f}% | {test_metrics.max_drawdown * 100:.2f}% | "
                f"{test_metrics.trade_count} |"
            )
        rendered += "\n".join(wf_lines) + "\n"
        print("\n".join(wf_lines))
    except ValueError as error:
        print(f"Walk-forward evaluation skipped: {error}")

    if args.output:
        output_path = ROOT / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        print(f"\nReport written to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
