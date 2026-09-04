import sys
import unittest
from decimal import Decimal
from pathlib import Path
from statistics import stdev

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.backtesting.metrics import calculate_metrics


class MetricsSampleStdevTests(unittest.TestCase):
    """Regression test for the population-vs-sample standard deviation bug.

    Before the fix, calculate_metrics used statistics.pstdev (population,
    N denominator) rather than statistics.stdev (sample, N-1 denominator).
    For the small samples typical of a backtest, pstdev systematically
    understates volatility, which in turn overstates the resulting Sharpe
    ratio (mean / volatility) — a systematic bias in reported risk-adjusted
    performance, not just a rounding difference.
    """

    def test_volatility_uses_sample_not_population_stdev(self) -> None:
        equity_curve = [Decimal("100000"), Decimal("101000"), Decimal("99000"), Decimal("102000")]
        metrics = calculate_metrics(
            initial_capital=Decimal("100000"),
            final_capital=Decimal("102000"),
            equity_curve=equity_curve,
            trade_pnls=[],
        )

        period_returns = [
            float((current - previous) / previous)
            for previous, current in zip(equity_curve, equity_curve[1:])
        ]
        expected_annualized_vol = stdev(period_returns) * (252**0.5)

        self.assertAlmostEqual(float(metrics.annualized_volatility), expected_annualized_vol, places=8)

    def test_non_positive_equity_point_raises_instead_of_silently_dropping(self) -> None:
        """Regression: a non-positive equity point must be surfaced, not silently skipped.

        Before the fix, an equity_curve entry of 0 (or negative) was simply
        skipped when computing period returns, silently shrinking the
        effective sample used for volatility/Sharpe with no signal that
        anomalous data was dropped.
        """

        equity_curve = [Decimal("100000"), Decimal("0"), Decimal("50000")]
        with self.assertRaises(ValueError):
            calculate_metrics(
                initial_capital=Decimal("100000"),
                final_capital=Decimal("50000"),
                equity_curve=equity_curve,
                trade_pnls=[],
            )

    def test_profit_factor_documentation_distinguishes_no_trades_from_all_losses(self) -> None:
        no_trades = calculate_metrics(
            initial_capital=Decimal("100000"),
            final_capital=Decimal("100000"),
            equity_curve=[Decimal("100000")],
            trade_pnls=[],
        )
        all_losses = calculate_metrics(
            initial_capital=Decimal("100000"),
            final_capital=Decimal("99000"),
            equity_curve=[Decimal("100000"), Decimal("99000")],
            trade_pnls=[Decimal("-1000")],
        )

        # Both report profit_factor == 0, but trade_count distinguishes them.
        self.assertEqual(no_trades.profit_factor, Decimal("0"))
        self.assertEqual(all_losses.profit_factor, Decimal("0"))
        self.assertEqual(no_trades.trade_count, 0)
        self.assertEqual(all_losses.trade_count, 1)


if __name__ == "__main__":
    unittest.main()
