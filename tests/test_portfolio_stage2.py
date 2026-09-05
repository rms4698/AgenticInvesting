import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data.models import Bar
from agentic_investing.portfolio import (
    FundamentalSnapshot,
    PortfolioBacktester,
    ScreeningConfig,
    build_portfolio_decisions,
    screen_instrument,
)
from agentic_investing.portfolio import load_fundamentals_json
from agentic_investing.portfolio import load_universe


def make_bars(instrument: str, count: int = 90) -> list[Bar]:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = []
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


def make_fundamentals(instrument: str, available_at: datetime) -> FundamentalSnapshot:
    return FundamentalSnapshot(
        instrument=instrument,
        exchange="NSE",
        available_at=available_at,
        source="test-fundamentals-2025-01-01",
        sector="TEST",
        market_cap=Decimal("1000000000"),
        pe_ratio=Decimal("20"),
        revenue_growth=Decimal("0.10"),
        return_on_equity=Decimal("0.15"),
        debt_to_equity=Decimal("0.20"),
    )


class PortfolioScreeningTests(unittest.TestCase):
    def test_screening_uses_only_available_fundamentals_and_builds_stop_target(self) -> None:
        bars = make_bars("TEST")
        fundamentals = make_fundamentals("TEST", bars[60].available_at)
        candidate = screen_instrument(
            bars,
            fundamentals,
            60,
            config=ScreeningConfig(min_average_volume=1, stop_atr_multiple=Decimal("2")),
        )

        self.assertIsNotNone(candidate)
        assert candidate is not None
        self.assertGreater(candidate.target_price, candidate.technical.close)
        self.assertLess(candidate.stop_price, candidate.technical.close)

    def test_future_fundamentals_are_rejected_to_prevent_lookahead(self) -> None:
        bars = make_bars("TEST")
        future = make_fundamentals("TEST", bars[61].available_at)
        candidate = screen_instrument(bars, future, 60, config=ScreeningConfig(min_average_volume=1))
        self.assertIsNone(candidate)

    def test_portfolio_decisions_rank_and_buy_new_candidates(self) -> None:
        bars = make_bars("TEST")
        fundamentals = make_fundamentals("TEST", bars[60].available_at)
        decisions = build_portfolio_decisions(
            {"TEST": bars},
            {"TEST": fundamentals},
            60,
            config=ScreeningConfig(min_average_volume=1),
        )
        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].action, "BUY")

    def test_fundamentals_json_loader_requires_timestamped_decimal_strings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "fundamentals.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "instrument": "TEST",
                            "exchange": "NSE",
                            "available_at": "2025-01-02T00:00:00+00:00",
                            "source": "test-source",
                            "sector": "TEST",
                            "pe_ratio": "20",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            loaded = load_fundamentals_json(path)
            self.assertEqual(loaded["NSE:TEST"].pe_ratio, Decimal("20"))

    def test_universe_loader_rejects_duplicates_and_loads_enabled_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "universe.json"
            path.write_text(
                json.dumps(
                    {
                        "version": "test",
                        "exchange": "NSE",
                        "instruments": [{"symbol": "TEST", "enabled": True}],
                    }
                ),
                encoding="utf-8",
            )
            universe = load_universe(path)
            self.assertEqual(universe.version, "test")
            self.assertEqual(universe.instruments[0].symbol, "TEST")

    def test_all_equity_universe_can_be_discovered_from_kite_at_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "universe.json"
            path.write_text(
                json.dumps(
                    {
                        "version": "test-all",
                        "exchange": "NSE",
                        "selection_mode": "all_equity",
                        "max_instruments_per_run": 50,
                        "instruments": [],
                    }
                ),
                encoding="utf-8",
            )
            universe = load_universe(path)
            self.assertEqual(universe.selection_mode, "all_equity")
            self.assertEqual(universe.max_instruments_per_run, 50)
            self.assertEqual(universe.instruments, ())


class PortfolioBacktesterTests(unittest.TestCase):
    def test_portfolio_backtest_uses_same_paper_execution_boundary(self) -> None:
        bars = make_bars("TEST")
        fundamentals = make_fundamentals("TEST", bars[0].available_at)
        result = PortfolioBacktester(
            screening_config=ScreeningConfig(min_average_volume=1),
        ).run({"TEST": bars}, {"TEST": fundamentals})

        self.assertEqual(result.initial_capital, Decimal("100000"))
        self.assertEqual(len(result.equity_curve), len(bars) + 1)
        self.assertGreaterEqual(result.metrics.trade_count, 1)
        self.assertTrue(all(value > 0 for value in result.equity_curve))


if __name__ == "__main__":
    unittest.main()
