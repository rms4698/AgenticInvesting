import sys
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.risk import RiskEngine, RiskLimits


def make_engine(**overrides) -> RiskEngine:
    return RiskEngine(RiskLimits(**overrides))


class RiskEngineMarkToMarketTests(unittest.TestCase):
    def test_kill_switch_trips_at_hard_drawdown(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"), hard_drawdown_fraction=Decimal("0.12"))
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

        engine.mark_to_market(Decimal("100000"), t0)
        self.assertFalse(engine.kill_switch_triggered)

        # Drawdown of 12% from the 100000 peak == 88000.
        engine.mark_to_market(Decimal("88000"), t0)
        self.assertTrue(engine.kill_switch_triggered)
        self.assertIn("hard drawdown breached", engine.kill_switch_reason or "")

    def test_kill_switch_does_not_trip_below_threshold(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"), hard_drawdown_fraction=Decimal("0.12"))
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)

        engine.mark_to_market(Decimal("100000"), t0)
        engine.mark_to_market(Decimal("89000"), t0)  # 11% drawdown
        self.assertFalse(engine.kill_switch_triggered)

    def test_kill_switch_blocks_new_positions_until_reset(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"))
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)
        engine.mark_to_market(Decimal("85000"), t0)  # breach

        decision = engine.evaluate_new_position(equity=Decimal("85000"), open_position_count=0)
        self.assertFalse(decision.approved)
        self.assertTrue(any("kill switch" in reason for reason in decision.reasons))

        engine.reset_kill_switch(reason="manual review completed")
        self.assertFalse(engine.kill_switch_triggered)
        self.assertEqual(engine.reset_log, ("manual review completed",))

    def test_reset_requires_non_empty_reason(self) -> None:
        engine = make_engine()
        with self.assertRaises(ValueError):
            engine.reset_kill_switch(reason="   ")


class RiskEngineLossLimitTests(unittest.TestCase):
    def test_daily_loss_limit_blocks_new_positions(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"), daily_loss_fraction=Decimal("0.01"))
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)

        decision = engine.evaluate_new_position(equity=Decimal("98900"), open_position_count=0)  # 1100 loss >= 1000
        self.assertFalse(decision.approved)
        self.assertTrue(any("daily loss" in reason for reason in decision.reasons))

    def test_monthly_loss_limit_blocks_new_positions(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"), monthly_loss_fraction=Decimal("0.05"))
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)

        decision = engine.evaluate_new_position(equity=Decimal("94000"), open_position_count=0)  # 6000 loss >= 5000
        self.assertFalse(decision.approved)
        self.assertTrue(any("monthly loss" in reason for reason in decision.reasons))

    def test_daily_baseline_resets_on_new_day(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"), daily_loss_fraction=Decimal("0.01"))
        day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        day2 = datetime(2026, 1, 2, tzinfo=timezone.utc)

        engine.mark_to_market(Decimal("100000"), day1)
        engine.mark_to_market(Decimal("99500"), day1)  # within daily limit
        engine.mark_to_market(Decimal("99500"), day2)  # new day baseline resets to 99500

        decision = engine.evaluate_new_position(equity=Decimal("99500"), open_position_count=0)
        self.assertTrue(decision.approved)

    def test_max_positions_limit_blocks_new_positions(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"), max_positions=2)
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        engine.mark_to_market(Decimal("100000"), t0)

        decision = engine.evaluate_new_position(equity=Decimal("100000"), open_position_count=2)
        self.assertFalse(decision.approved)
        self.assertTrue(any("max open positions" in reason for reason in decision.reasons))


class RiskEngineSizingTests(unittest.TestCase):
    def test_sizing_respects_smallest_applicable_limit(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"))

        quantity = engine.size_new_position(
            cash=Decimal("100000"),
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )
        # Risk budget: min(500, 2000) / (100 * 0.05) = 500 / 5 = 100 shares.
        self.assertEqual(quantity, 100)

    def test_sizing_is_bounded_by_available_cash(self) -> None:
        engine = make_engine(account_capital=Decimal("100000"))

        quantity = engine.size_new_position(
            cash=Decimal("300"),
            fill_price=Decimal("100"),
            stop_distance_fraction=Decimal("0.05"),
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
        )
        self.assertEqual(quantity, 3)

    def test_sizing_rejects_non_positive_price(self) -> None:
        engine = make_engine()
        with self.assertRaises(ValueError):
            engine.size_new_position(
                cash=Decimal("1000"),
                fill_price=Decimal("0"),
                stop_distance_fraction=Decimal("0.05"),
                initial_capital=Decimal("100000"),
                commission_rate=Decimal("0"),
            )


if __name__ == "__main__":
    unittest.main()
