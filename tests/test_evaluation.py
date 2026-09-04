import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.backtesting import (
    BacktestConfig,
    CostScenario,
    chronological_split,
    evaluate_train_test,
    evaluate_walk_forward,
    generate_walk_forward_windows,
    render_validation_report,
)
from agentic_investing.data.models import Bar
from agentic_investing.strategies import SmaCrossoverStrategy


def make_bars(size: int = 20) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    closes = ["10", "9", "8", "9", "10", "11", "12", "11", "10", "9"] * 3
    bars = []
    for index, close_text in enumerate(closes[:size]):
        close = Decimal(close_text)
        timestamp = start + timedelta(days=index)
        bars.append(Bar("TEST", "NSE", "1d", timestamp, timestamp, close, close, close, close, 100000))
    return bars


class EvaluationTests(unittest.TestCase):
    def test_chronological_split_preserves_order(self) -> None:
        bars = make_bars(10)
        split = chronological_split(bars, 0.6)

        self.assertEqual(split.split_index, 6)
        self.assertEqual(split.train[-1].timestamp, bars[5].timestamp)
        self.assertEqual(split.test[0].timestamp, bars[6].timestamp)

    def test_walk_forward_windows_are_chronological(self) -> None:
        windows = generate_walk_forward_windows(20, train_size=8, test_size=4, step=4)

        self.assertEqual(len(windows), 3)
        self.assertEqual(windows[0].train_end, windows[0].test_start)
        self.assertEqual(windows[1].train_start, 4)
        self.assertLess(windows[1].test_start, windows[2].test_start)

    def test_train_test_report_has_benchmarks_and_cost_sensitivity(self) -> None:
        report = evaluate_train_test(
            make_bars(),
            SmaCrossoverStrategy(fast_period=2, slow_period=3),
            split=0.6,
            config=BacktestConfig(commission_rate=Decimal("0"), slippage_rate=Decimal("0")),
            cost_scenarios=(
                CostScenario("base", Decimal("0"), Decimal("0")),
                CostScenario("stress", Decimal("0.001"), Decimal("0.002")),
            ),
        )
        rendered = render_validation_report(report)

        self.assertGreaterEqual(report.test_result.metrics.trade_count, 0)
        self.assertEqual(report.test_cash.metrics.total_return, Decimal("0"))
        self.assertEqual(len(report.cost_sensitivity), 2)
        self.assertIn("Test buy-and-hold", rendered)
        self.assertIn("Cost sensitivity", rendered)

    def test_walk_forward_returns_one_result_per_window(self) -> None:
        runs = evaluate_walk_forward(
            make_bars(),
            SmaCrossoverStrategy(fast_period=2, slow_period=3),
            train_size=8,
            test_size=4,
        )

        self.assertEqual(len(runs), 3)
        self.assertTrue(all(run.test_result.initial_capital == Decimal("100000") for run in runs))


if __name__ == "__main__":
    unittest.main()
