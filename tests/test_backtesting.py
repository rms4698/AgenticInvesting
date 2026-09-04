import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.backtesting import BacktestConfig, Backtester
from agentic_investing.data.models import Bar
from agentic_investing.strategies import SmaCrossoverStrategy


def make_bars(closes: list[str]) -> list[Bar]:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    bars = []
    for index, close_text in enumerate(closes):
        close = Decimal(close_text)
        timestamp = start + timedelta(days=index)
        bars.append(
            Bar(
                instrument="TEST",
                exchange="NSE",
                timeframe="1d",
                timestamp=timestamp,
                available_at=timestamp,
                open=close,
                high=close,
                low=close,
                close=close,
                volume=100000,
            )
        )
    return bars


class StrategyTests(unittest.TestCase):
    def test_sma_strategy_uses_only_closed_bars(self) -> None:
        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)
        signals = strategy.generate_signals(make_bars(["10", "9", "8", "9", "10", "11", "10", "8"]))

        self.assertEqual([signal.action for signal in signals], ["BUY", "SELL"])
        self.assertEqual(signals[0].timestamp, datetime(2026, 1, 5, tzinfo=timezone.utc))

    def test_invalid_strategy_periods_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SmaCrossoverStrategy(fast_period=3, slow_period=3)


class BacktesterTests(unittest.TestCase):
    def test_backtest_executes_signals_on_next_bar_open_and_accounts_for_costs(self) -> None:
        bars = make_bars(["10", "9", "8", "9", "10", "11", "10"])
        result = Backtester(
            BacktestConfig(
                initial_capital=Decimal("100000"),
                commission_rate=Decimal("0"),
                slippage_rate=Decimal("0"),
                stop_distance_fraction=Decimal("0.05"),
            )
        ).run(bars, SmaCrossoverStrategy(fast_period=2, slow_period=3))

        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_time, bars[5].timestamp)
        self.assertEqual(result.trades[0].entry_price, Decimal("11"))
        self.assertEqual(result.trades[0].exit_price, Decimal("10"))
        self.assertLess(result.final_capital, result.initial_capital)
        self.assertEqual(result.metrics.trade_count, 1)

    def test_backtest_rejects_lookahead_data(self) -> None:
        bars = make_bars(["10", "11", "12"])
        bars[1] = Bar(
            instrument=bars[1].instrument,
            exchange=bars[1].exchange,
            timeframe=bars[1].timeframe,
            timestamp=bars[1].timestamp,
            available_at=bars[1].timestamp - timedelta(minutes=1),
            open=bars[1].open,
            high=bars[1].high,
            low=bars[1].low,
            close=bars[1].close,
            volume=bars[1].volume,
        )

        with self.assertRaises(ValueError):
            Backtester().run(bars, SmaCrossoverStrategy())


if __name__ == "__main__":
    unittest.main()
