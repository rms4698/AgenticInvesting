import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.backtesting import BacktestConfig, StrategyCandidate, compare_strategies
from agentic_investing.data.models import Bar
from agentic_investing.strategies import DonchianBreakoutStrategy, SmaCrossoverStrategy


def make_bars(count: int = 220) -> list[Bar]:
    bars: list[Bar] = []
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for index in range(count):
        cycle = index % 80
        close = Decimal(str(100 + (cycle if cycle < 40 else 80 - cycle)))
        bars.append(
            Bar(
                instrument="TEST",
                exchange="NSE",
                timeframe="1d",
                timestamp=start + timedelta(days=index),
                available_at=start + timedelta(days=index + 1),
                open=close,
                high=close + Decimal("1"),
                low=close - Decimal("1"),
                close=close,
                volume=1000,
            )
        )
    return bars


class StrategySelectionTests(unittest.TestCase):
    def test_compares_multiple_strategies_using_walk_forward_metrics(self) -> None:
        selection = compare_strategies(
            make_bars(),
            [
                StrategyCandidate("sma_3_8", lambda: SmaCrossoverStrategy(fast_period=3, slow_period=8)),
                StrategyCandidate("donchian_10", lambda: DonchianBreakoutStrategy(lookback_period=10)),
            ],
            train_size=80,
            test_size=40,
            config=BacktestConfig(enable_stop_loss=False, enable_target_exit=False),
        )

        self.assertEqual(len(selection.scores), 2)
        self.assertEqual({score.name for score in selection.scores}, {"sma_3_8", "donchian_10"})
        self.assertTrue(all(score.test_windows > 0 for score in selection.scores))
        self.assertIn(selection.selected_name, {None, "sma_3_8", "donchian_10"})

    def test_selection_fails_closed_when_no_candidate_meets_gate(self) -> None:
        selection = compare_strategies(
            make_bars(),
            [StrategyCandidate("sma_3_8", lambda: SmaCrossoverStrategy(fast_period=3, slow_period=8))],
            train_size=80,
            test_size=40,
            min_positive_windows=999,
            config=BacktestConfig(enable_stop_loss=False, enable_target_exit=False),
        )

        self.assertIsNone(selection.selected_name)
        self.assertFalse(selection.scores[0].eligible)

    def test_empty_candidate_list_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compare_strategies(make_bars(), [], train_size=80, test_size=40)


if __name__ == "__main__":
    unittest.main()
