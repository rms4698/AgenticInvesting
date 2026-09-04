import sys
import unittest
from decimal import Decimal
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.risk import RiskLimits


class RiskLimitsTests(unittest.TestCase):
    def test_initial_profile_matches_agreed_defaults(self) -> None:
        limits = RiskLimits()

        self.assertEqual(limits.account_capital, Decimal("100000"))
        self.assertEqual(limits.max_deployed_capital, Decimal("80000"))
        self.assertEqual(limits.risk_per_trade, Decimal("500"))
        self.assertEqual(limits.max_open_portfolio_risk, Decimal("2000"))
        self.assertEqual(limits.daily_loss_limit, Decimal("1000"))
        self.assertEqual(limits.monthly_loss_limit, Decimal("5000"))
        self.assertEqual(limits.hard_drawdown_limit, Decimal("12000"))
        self.assertEqual(limits.max_leverage, Decimal("1"))

    def test_invalid_limits_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            RiskLimits(risk_per_trade_fraction=Decimal("0.03"))

        with self.assertRaises(ValueError):
            RiskLimits(drawdown_review_fraction=Decimal("0.15"))


if __name__ == "__main__":
    unittest.main()
