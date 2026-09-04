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
from agentic_investing.risk import RiskLimits
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


def make_ohlc_bar(instrument: str, timestamp: datetime, *, open_: str, high: str, low: str, close: str) -> Bar:
    """Build a Bar with independently controllable OHLC, for stop/target tests."""

    return Bar(
        instrument=instrument,
        exchange="NSE",
        timeframe="1d",
        timestamp=timestamp,
        available_at=timestamp,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=100000,
    )


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

    def test_kill_switch_trips_on_crash_and_blocks_later_re_entry(self) -> None:
        # With aggressive sizing (full deployment), BUY@day6 entering at 11
        # then crashing to 1 by day8 breaches the 12% hard-drawdown kill
        # switch. SELL@day8 still closes the open position, but the later
        # BUY@day10 signal must be blocked because the kill switch remains
        # tripped for the rest of the backtest.
        bars = make_bars(["10", "9", "8", "9", "10", "11", "1", "1", "5", "8", "10", "15", "20"])
        aggressive_limits = RiskLimits(
            account_capital=Decimal("100000"),
            risk_per_trade_fraction=Decimal("0.5"),
            max_open_portfolio_risk_fraction=Decimal("0.5"),
            max_single_position_fraction=Decimal("0.5"),
            capital_deployment_fraction=Decimal("1.0"),
        )
        result = Backtester(
            BacktestConfig(
                initial_capital=Decimal("100000"),
                commission_rate=Decimal("0"),
                slippage_rate=Decimal("0"),
                stop_distance_fraction=Decimal("0.05"),
            ),
            risk_limits=aggressive_limits,
        ).run(bars, SmaCrossoverStrategy(fast_period=2, slow_period=3))

        self.assertTrue(result.kill_switch_triggered)
        self.assertIn("hard drawdown breached", result.kill_switch_reason or "")
        # Two BUY signals occur, but only the first trade executes; the
        # second is blocked by the tripped kill switch.
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].entry_price, Decimal("11"))
        self.assertEqual(result.trades[0].exit_price, Decimal("1"))
        # No new position opens after the block, so equity stays flat.
        self.assertEqual(result.equity_curve[-1], result.equity_curve[-2])

    def test_forced_final_bar_exit_uses_close_with_slippage_and_no_duplicate_point(self) -> None:
        """Regression: ending the backtest while still holding a position.

        Before the fix, force-closing appended a *second* equity-curve point
        for the same final-bar timestamp (corrupting period-return-based
        metrics), used the last bar's *open* with slippage as the exit price
        (inconsistent with the close-based mark-to-market moments earlier in
        the same iteration), and paid *zero* slippage if the position had
        been entered on that very last bar. The fix always exits at the last
        bar's close with the same slippage as every other exit, and replaces
        (rather than appends to) the final mark-to-market equity point.
        """

        bars = make_bars(["10", "9", "8", "9", "10", "11"])  # BUY signal at day5, no SELL before data ends
        config = BacktestConfig(
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
            slippage_rate=Decimal("0.01"),
            stop_distance_fraction=Decimal("0.05"),
        )
        result = Backtester(config).run(bars, SmaCrossoverStrategy(fast_period=2, slow_period=3))

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        # Exit priced at the last bar's close with slippage, same basis as
        # every other exit in the system.
        expected_exit_price = Decimal("11") * (Decimal("1") - Decimal("0.01"))
        self.assertEqual(trade.exit_price, expected_exit_price)
        self.assertEqual(trade.exit_time, bars[-1].timestamp)
        # Exactly one equity-curve point per bar plus the initial point — no
        # extra spurious point appended for the forced-close event.
        self.assertEqual(len(result.equity_curve), len(bars) + 1)
        # The final point is the realized post-liquidation cash, not the
        # unrealized close-mark value that was there before the forced exit.
        self.assertEqual(result.equity_curve[-1], result.final_capital)


class StopLossAndTargetTests(unittest.TestCase):
    """Regression tests for independent per-position stop-loss/profit-target exits.

    Before this feature, the only exit mechanism was the strategy's own
    (lagging) crossover SELL signal — a large adverse move could ride for
    days before the strategy decided to exit, and there was no way to lock
    in a profit target independent of the signal either.
    """

    def test_stop_loss_closes_position_intrabar_before_the_strategy_would_exit(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # BUY signal decided at day4 (close crossover), executes at day5's
        # open (price 10). Day6's bar gaps/dips intrabar to 9 (a 10% drop,
        # breaching the 5% stop) while its close (9.5) stays high enough that
        # the SMA crossover would NOT yet emit a SELL — proving the stop
        # fires independently of (and before) the lagging signal.
        bars = [
            make_ohlc_bar("TEST", start, open_="10", high="10", low="10", close="10"),
            make_ohlc_bar("TEST", start + timedelta(days=1), open_="9", high="9", low="9", close="9"),
            make_ohlc_bar("TEST", start + timedelta(days=2), open_="8", high="8", low="8", close="8"),
            make_ohlc_bar("TEST", start + timedelta(days=3), open_="9", high="9", low="9", close="9"),
            make_ohlc_bar("TEST", start + timedelta(days=4), open_="10", high="10", low="10", close="10"),
            make_ohlc_bar("TEST", start + timedelta(days=5), open_="10", high="10.5", low="9.9", close="10"),
            make_ohlc_bar("TEST", start + timedelta(days=6), open_="9.8", high="9.8", low="9.0", close="9.5"),
        ]
        config = BacktestConfig(
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
            stop_distance_fraction=Decimal("0.05"),
            stop_loss_distance_fraction=Decimal("0.05"),
        )
        result = Backtester(config).run(bars, SmaCrossoverStrategy(fast_period=2, slow_period=3))

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, "STOP_LOSS")
        self.assertEqual(trade.exit_time, bars[6].timestamp)
        # Entry at day5 open (10) with a 5% stop -> stop price 9.5; day7's
        # low (9.0) breaches it, day7's open (9.8) is above the stop, so the
        # exit fills at the stop price itself (no gap-through).
        self.assertEqual(trade.exit_price, Decimal("9.5"))

    def test_target_exit_closes_position_intrabar_at_profit_target(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = [
            make_ohlc_bar("TEST", start, open_="10", high="10", low="10", close="10"),
            make_ohlc_bar("TEST", start + timedelta(days=1), open_="9", high="9", low="9", close="9"),
            make_ohlc_bar("TEST", start + timedelta(days=2), open_="8", high="8", low="8", close="8"),
            make_ohlc_bar("TEST", start + timedelta(days=3), open_="9", high="9", low="9", close="9"),
            make_ohlc_bar("TEST", start + timedelta(days=4), open_="10", high="10", low="10", close="10"),
            # Entry at day5 open (10). Default minimum_reward_risk is 1.5x,
            # stop_distance_fraction is 5% -> target = 10 * (1 + 0.05*1.5) = 10.75.
            make_ohlc_bar("TEST", start + timedelta(days=5), open_="10", high="10.5", low="9.9", close="10.2"),
            make_ohlc_bar("TEST", start + timedelta(days=6), open_="10.3", high="10.9", low="10.2", close="10.6"),
        ]
        config = BacktestConfig(
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
            stop_distance_fraction=Decimal("0.05"),
            stop_loss_distance_fraction=Decimal("0.05"),
        )
        result = Backtester(config).run(bars, SmaCrossoverStrategy(fast_period=2, slow_period=3))

        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        self.assertEqual(trade.exit_reason, "TARGET")
        self.assertEqual(trade.exit_time, bars[6].timestamp)
        self.assertEqual(trade.exit_price, Decimal("10.75"))

    def test_stop_loss_and_target_can_be_disabled(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = [
            make_ohlc_bar("TEST", start, open_="10", high="10", low="10", close="10"),
            make_ohlc_bar("TEST", start + timedelta(days=1), open_="9", high="9", low="9", close="9"),
            make_ohlc_bar("TEST", start + timedelta(days=2), open_="8", high="8", low="8", close="8"),
            make_ohlc_bar("TEST", start + timedelta(days=3), open_="9", high="9", low="9", close="9"),
            make_ohlc_bar("TEST", start + timedelta(days=4), open_="10", high="10", low="10", close="10"),
            # Would breach a 5% stop intrabar (low 9.0) if stop-loss were enabled.
            make_ohlc_bar("TEST", start + timedelta(days=5), open_="10", high="10.5", low="9.0", close="10"),
            make_ohlc_bar("TEST", start + timedelta(days=6), open_="10", high="10", low="8", close="8"),
        ]
        config = BacktestConfig(
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
            stop_distance_fraction=Decimal("0.05"),
            enable_stop_loss=False,
            enable_target_exit=False,
        )
        result = Backtester(config).run(bars, SmaCrossoverStrategy(fast_period=2, slow_period=3))

        # No stop/target exit despite the intrabar breach; the position is
        # still open at the end and gets force-liquidated instead.
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].exit_reason, "FORCED_LIQUIDATION")


if __name__ == "__main__":
    unittest.main()
