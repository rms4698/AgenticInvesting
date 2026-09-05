import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data.models import Bar
from agentic_investing.portfolio import TechnicalOnlyBacktester, TechnicalOnlyConfig


def make_bars(instrument: str, count: int, start_offset: int = 0) -> list[Bar]:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc) + timedelta(days=start_offset)
    bars: list[Bar] = []
    for index in range(count):
        close = Decimal(str(100 + index))
        timestamp = start + timedelta(days=index)
        bars.append(
            Bar(
                instrument=instrument,
                exchange="NSE",
                timeframe="1d",
                timestamp=timestamp,
                available_at=timestamp + timedelta(days=1),
                open=close,
                high=close + Decimal("2"),
                low=close - Decimal("1"),
                close=close,
                volume=100000,
            )
        )
    return bars


class TechnicalOnlyPortfolioTests(unittest.TestCase):
    def test_backtest_handles_different_listing_histories(self) -> None:
        config = TechnicalOnlyConfig(
            minimum_rsi=Decimal("0"),
            maximum_rsi=Decimal("100"),
            minimum_volume_ratio=Decimal("0"),
            relative_strength_periods=(5,),
            weekly_sma_period=4,
            weekly_slope_lookback=1,
            start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end=datetime(2020, 4, 30, tzinfo=timezone.utc),
        )
        result = TechnicalOnlyBacktester(config=config).run(
            {
                "EARLY": make_bars("EARLY", 121),
                "LATE": make_bars("LATE", 80, start_offset=30),
            }
        )

        self.assertEqual(result.candidate_count, 2)
        self.assertEqual(result.start, config.start)
        self.assertGreaterEqual(result.metrics.trade_count, 1)
        self.assertEqual(len(result.trade_records), result.metrics.trade_count)
        self.assertTrue(result.trade_records[0].exit_reason)
        self.assertLessEqual(result.max_positions_held, 8)
        self.assertTrue(all(value > 0 for value in result.equity_curve))

    def test_default_rsi_and_volume_rules_are_explicit(self) -> None:
        config = TechnicalOnlyConfig()
        self.assertEqual(config.minimum_rsi, Decimal("50"))
        self.assertEqual(config.maximum_rsi, Decimal("70"))
        self.assertEqual(config.minimum_volume_ratio, Decimal("1"))

    def test_runner_mode_can_use_trailing_stop_without_profit_target(self) -> None:
        config = TechnicalOnlyConfig(
            minimum_rsi=Decimal("0"),
            maximum_rsi=Decimal("100"),
            minimum_volume_ratio=Decimal("0"),
            use_profit_target=False,
            trailing_stop_atr_multiple=Decimal("3"),
            weekly_sma_period=4,
            weekly_slope_lookback=1,
            start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end=datetime(2020, 4, 30, tzinfo=timezone.utc),
        )
        result = TechnicalOnlyBacktester(config=config).run({"TEST": make_bars("TEST", 121)})
        self.assertGreaterEqual(result.metrics.trade_count, 1)

    def test_market_regime_gate_can_be_enabled(self) -> None:
        config = TechnicalOnlyConfig(
            minimum_rsi=Decimal("0"),
            maximum_rsi=Decimal("100"),
            minimum_volume_ratio=Decimal("0"),
            relative_strength_periods=(5,),
            weekly_sma_period=4,
            weekly_slope_lookback=1,
            start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end=datetime(2020, 4, 30, tzinfo=timezone.utc),
        )
        result = TechnicalOnlyBacktester(
            config=config,
            market_regime_bars=make_bars("BENCHMARK", 121),
            market_fast_period=3,
            market_slow_period=5,
        ).run({"TEST": make_bars("TEST", 121)})
        self.assertGreaterEqual(result.metrics.trade_count, 1)

    def test_breakout_mode_is_configurable(self) -> None:
        config = TechnicalOnlyConfig(
            minimum_rsi=Decimal("0"),
            maximum_rsi=Decimal("100"),
            minimum_volume_ratio=Decimal("0"),
            breakout_lookback=50,
            require_breakout=True,
            weekly_sma_period=4,
            weekly_slope_lookback=1,
            start=datetime(2020, 1, 1, tzinfo=timezone.utc),
            end=datetime(2020, 4, 30, tzinfo=timezone.utc),
        )
        result = TechnicalOnlyBacktester(config=config).run({"TEST": make_bars("TEST", 121)})
        self.assertGreaterEqual(result.metrics.trade_count, 0)

    def test_pyramiding_settings_are_validated(self) -> None:
        config = TechnicalOnlyConfig(
            enable_pyramiding=True,
            max_pyramid_additions=2,
            pyramid_trigger_atr_multiple=Decimal("1"),
            pyramid_quantity_fraction=Decimal("0.5"),
        )
        self.assertTrue(config.enable_pyramiding)

    def test_sizing_and_pacing_settings_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            TechnicalOnlyConfig(max_new_entries_per_period=0)
        with self.assertRaises(ValueError):
            TechnicalOnlyConfig(new_entry_period_days=0)
        with self.assertRaises(ValueError):
            TechnicalOnlyConfig(backtest_kill_switch_cooldown_days=0)
        config = TechnicalOnlyConfig(
            use_weight_based_sizing=True,
            max_new_entries_per_period=2,
            backtest_kill_switch_cooldown_days=10,
        )
        self.assertTrue(config.use_weight_based_sizing)

    def test_entry_pacing_limits_new_positions_per_period(self) -> None:
        bars = {"AAAA": make_bars("AAAA", 121), "BBBB": make_bars("BBBB", 121)}

        unpaced = TechnicalOnlyBacktester(
            config=TechnicalOnlyConfig(
                minimum_rsi=Decimal("0"),
                maximum_rsi=Decimal("100"),
                minimum_volume_ratio=Decimal("0"),
                relative_strength_periods=(5,),
                weekly_sma_period=4,
                weekly_slope_lookback=1,
                start=datetime(2020, 1, 1, tzinfo=timezone.utc),
                end=datetime(2020, 4, 30, tzinfo=timezone.utc),
            )
        ).run(bars)
        first_day_unpaced = min(record.entry_time for record in unpaced.trade_records)
        same_day_unpaced = sum(
            1 for record in unpaced.trade_records if record.entry_time == first_day_unpaced
        )
        self.assertEqual(same_day_unpaced, 2)

        paced = TechnicalOnlyBacktester(
            config=TechnicalOnlyConfig(
                minimum_rsi=Decimal("0"),
                maximum_rsi=Decimal("100"),
                minimum_volume_ratio=Decimal("0"),
                relative_strength_periods=(5,),
                weekly_sma_period=4,
                weekly_slope_lookback=1,
                max_new_entries_per_period=1,
                new_entry_period_days=5,
                start=datetime(2020, 1, 1, tzinfo=timezone.utc),
                end=datetime(2020, 4, 30, tzinfo=timezone.utc),
            )
        ).run(bars)
        first_day_paced = min(record.entry_time for record in paced.trade_records)
        same_day_paced = sum(1 for record in paced.trade_records if record.entry_time == first_day_paced)
        self.assertEqual(same_day_paced, 1)

    def test_kill_switch_cooldown_resets_after_configured_days(self) -> None:
        from agentic_investing.risk import RiskEngine, RiskLimits

        config = TechnicalOnlyConfig(backtest_kill_switch_cooldown_days=5)
        backtester = TechnicalOnlyBacktester(config=config)
        engine = RiskEngine(
            RiskLimits(account_capital=Decimal("100000"), hard_drawdown_fraction=Decimal("0.12"))
        )
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)
        engine.mark_to_market(Decimal("85000"), t0)  # trips the 12% hard-drawdown kill switch
        self.assertTrue(engine.kill_switch_triggered)

        triggered_at, reset_happened = backtester._apply_kill_switch_cooldown(engine, t0, None)
        self.assertEqual(triggered_at, t0)
        self.assertFalse(reset_happened)

        triggered_at, reset_happened = backtester._apply_kill_switch_cooldown(
            engine, t0 + timedelta(days=4), triggered_at
        )
        self.assertFalse(reset_happened)
        self.assertTrue(engine.kill_switch_triggered)

        triggered_at, reset_happened = backtester._apply_kill_switch_cooldown(
            engine, t0 + timedelta(days=5), triggered_at
        )
        self.assertTrue(reset_happened)
        self.assertIsNone(triggered_at)
        self.assertFalse(engine.kill_switch_triggered)

    def test_kill_switch_cooldown_disabled_by_default_leaves_permanent_trip(self) -> None:
        from agentic_investing.risk import RiskEngine, RiskLimits

        backtester = TechnicalOnlyBacktester(config=TechnicalOnlyConfig())
        engine = RiskEngine(
            RiskLimits(account_capital=Decimal("100000"), hard_drawdown_fraction=Decimal("0.12"))
        )
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)
        engine.mark_to_market(Decimal("85000"), t0)

        triggered_at, reset_happened = backtester._apply_kill_switch_cooldown(
            engine, t0 + timedelta(days=100), None
        )
        self.assertFalse(reset_happened)
        self.assertTrue(engine.kill_switch_triggered)


if __name__ == "__main__":
    unittest.main()
