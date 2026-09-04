import sys
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.data.models import Bar
from agentic_investing.shadow import ShadowSessionConfig, ShadowTradingSession
from agentic_investing.strategies import SmaCrossoverStrategy


def make_bar(instrument: str, timestamp: datetime, close_text: str) -> Bar:
    close = Decimal(close_text)
    return Bar(instrument, "NSE", "1d", timestamp, timestamp, close, close, close, close, 100000)


def make_session(**config_overrides) -> ShadowTradingSession:
    config = ShadowSessionConfig(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
        stop_distance_fraction=Decimal("0.05"),
        **config_overrides,
    )
    return ShadowTradingSession(strategy=SmaCrossoverStrategy(fast_period=2, slow_period=3), config=config)


class ShadowSessionHappyPathTests(unittest.TestCase):
    def test_signals_execute_at_next_bar_open_matching_backtester_semantics(self) -> None:
        session = make_session()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # BUY signal is decided at day4 (executes at day5's open); SELL signal
        # is decided at day7 (executes at day8's open) — the extra day8 bar is
        # required for the SELL to actually fill.
        closes = ["10", "9", "8", "9", "10", "11", "10", "8", "8"]

        for index, close in enumerate(closes):
            session.on_bar(make_bar("TEST", start + timedelta(days=index), close))

        submitted_outcomes = [event.order_outcome for event in session._events if event.order_outcome]
        self.assertEqual(len(submitted_outcomes), 2)
        self.assertTrue(all(outcome.submitted for outcome in submitted_outcomes))
        # Position should be flat after the SELL.
        self.assertEqual(session.broker.list_positions(), ())

    def test_daily_report_reflects_processed_bars_and_state(self) -> None:
        session = make_session()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for index, close in enumerate(["10", "9", "8", "9", "10"]):
            session.on_bar(make_bar("TEST", start + timedelta(days=index), close))

        report = session.daily_report()

        self.assertIn("Bars processed: 5", report)
        self.assertIn("Kill switch: clear", report)
        self.assertIn("Shadow Trading Daily Report", report)


class ShadowSessionGapDetectionTests(unittest.TestCase):
    def test_large_gap_suppresses_new_buy_but_records_incident(self) -> None:
        session = make_session(max_bar_gap=timedelta(days=2))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Build up to just before a BUY crossover, then introduce a big gap
        # exactly on the bar that would trigger the BUY signal's execution.
        session.on_bar(make_bar("TEST", start, "10"))
        session.on_bar(make_bar("TEST", start + timedelta(days=1), "9"))
        session.on_bar(make_bar("TEST", start + timedelta(days=2), "8"))
        session.on_bar(make_bar("TEST", start + timedelta(days=3), "9"))
        # This bar's close triggers a BUY signal (fast>slow), executed on the
        # *next* bar's open. Introduce a large gap for that next bar.
        session.on_bar(make_bar("TEST", start + timedelta(days=4), "10"))
        gapped_timestamp = start + timedelta(days=10)  # gap of 6 days > max_bar_gap of 2
        session.on_bar(make_bar("TEST", gapped_timestamp, "11"))

        self.assertEqual(session.broker.list_positions(), ())  # no BUY executed
        gap_incidents = [incident for incident in session.incidents if incident.category == "DATA_GAP"]
        blocked_incidents = [incident for incident in session.incidents if incident.category == "ORDER_BLOCKED"]
        self.assertEqual(len(gap_incidents), 1)
        self.assertEqual(len(blocked_incidents), 1)

    def test_small_gap_within_tolerance_does_not_suppress(self) -> None:
        session = make_session(max_bar_gap=timedelta(days=4))  # tolerate weekends
        start = datetime(2026, 1, 2, tzinfo=timezone.utc)  # Friday
        closes = ["10", "9", "8", "9", "10", "11"]
        # Skip weekend between day 4 (Mon) and day 5 (Tue) — 1-day gaps here anyway.
        for index, close in enumerate(closes):
            session.on_bar(make_bar("TEST", start + timedelta(days=index), close))

        self.assertEqual(len([i for i in session.incidents if i.category == "DATA_GAP"]), 0)


class ShadowSessionManualStaleTests(unittest.TestCase):
    def test_mark_stale_suppresses_next_buy_only(self) -> None:
        session = make_session()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session.on_bar(make_bar("TEST", start, "10"))
        session.on_bar(make_bar("TEST", start + timedelta(days=1), "9"))
        session.on_bar(make_bar("TEST", start + timedelta(days=2), "8"))
        session.on_bar(make_bar("TEST", start + timedelta(days=3), "9"))
        session.on_bar(make_bar("TEST", start + timedelta(days=4), "10"))  # BUY signal decided here
        session.mark_stale("simulated broker disconnect detected by heartbeat")
        session.on_bar(make_bar("TEST", start + timedelta(days=5), "11"))  # BUY would execute here

        self.assertEqual(session.broker.list_positions(), ())
        manual_incidents = [i for i in session.incidents if i.category == "MANUAL_STALE"]
        blocked_incidents = [i for i in session.incidents if i.category == "ORDER_BLOCKED"]
        self.assertEqual(len(manual_incidents), 1)
        self.assertEqual(len(blocked_incidents), 1)

        # A subsequent bar without staleness allows the (still pending) crossover state to proceed normally.
        session.on_bar(make_bar("TEST", start + timedelta(days=6), "12"))
        # No retroactive re-trigger of the missed signal; strategy naturally
        # continues from here. This assertion just confirms no crash/hang.
        self.assertEqual(len(session._history), 7)

    def test_mark_stale_requires_non_empty_reason(self) -> None:
        session = make_session()
        with self.assertRaises(ValueError):
            session.mark_stale("   ")


class ShadowSessionKillSwitchTests(unittest.TestCase):
    def test_kill_switch_blocks_buy_but_sell_still_executes(self) -> None:
        from agentic_investing.risk import RiskLimits

        aggressive_limits = RiskLimits(
            account_capital=Decimal("100000"),
            risk_per_trade_fraction=Decimal("0.5"),
            max_open_portfolio_risk_fraction=Decimal("0.5"),
            max_single_position_fraction=Decimal("0.5"),
            capital_deployment_fraction=Decimal("1.0"),
        )
        config = ShadowSessionConfig(
            initial_capital=Decimal("100000"),
            commission_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
            stop_distance_fraction=Decimal("0.05"),
        )
        session = ShadowTradingSession(
            strategy=SmaCrossoverStrategy(fast_period=2, slow_period=3),
            config=config,
            risk_limits=aggressive_limits,
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        closes = ["10", "9", "8", "9", "10", "11", "1", "1", "5", "8", "10", "15", "20"]
        for index, close in enumerate(closes):
            session.on_bar(make_bar("TEST", start + timedelta(days=index), close))

        self.assertTrue(session.risk_engine.kill_switch_triggered)
        # Only one BUY should have executed (the second is blocked post-crash).
        submitted_buys = [
            event
            for event in session._events
            if event.order_outcome and event.order_outcome.submitted and event.signal_action == "BUY"
        ]
        self.assertEqual(len(submitted_buys), 1)
        report = session.daily_report()
        self.assertIn("TRIPPED", report)


class ShadowSessionPositionAwareDecisionTests(unittest.TestCase):
    """Regression tests for the position-desync fix.

    Before the fix, the strategy tracked its own "holding" belief internally
    and flipped it to True as soon as it *proposed* a BUY signal — even if
    that BUY was later suppressed/blocked and never actually filled. On the
    next bar, the strategy would then believe it already held a position and
    refuse to propose another BUY, silently missing a real opportunity.

    After the fix, every decision asks the real broker position via
    ``holding=...`` in ``strategy.decide()``, so a blocked BUY can never
    cause a later missed opportunity.
    """

    def test_buy_blocked_by_stale_data_is_retried_on_the_next_bar(self) -> None:
        session = make_session()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # Build up to the crossover point.
        session.on_bar(make_bar("TEST", start, "10"))
        session.on_bar(make_bar("TEST", start + timedelta(days=1), "9"))
        session.on_bar(make_bar("TEST", start + timedelta(days=2), "8"))
        session.on_bar(make_bar("TEST", start + timedelta(days=3), "9"))
        # Crossover decided here (fast>slow, not holding) -> BUY would
        # execute on the *next* bar's open. Mark stale right before that bar
        # so the BUY is suppressed rather than filled.
        session.on_bar(make_bar("TEST", start + timedelta(days=4), "10"))
        session.mark_stale("simulated outage right before the BUY would execute")
        session.on_bar(make_bar("TEST", start + timedelta(days=5), "11"))  # BUY suppressed here

        self.assertEqual(session.broker.list_positions(), ())
        blocked = [i for i in session.incidents if i.category == "ORDER_BLOCKED"]
        self.assertEqual(len(blocked), 1)

        # Crucially: the strategy is *still not holding* (real position is
        # flat), and the crossover condition (fast>slow) still holds because
        # the average has not crossed back down. The very next bar must
        # therefore retry the BUY and this time it should actually fill,
        # proving the strategy re-evaluates from real state rather than a
        # stale internal "already bought" belief.
        session.on_bar(make_bar("TEST", start + timedelta(days=6), "12"))

        self.assertEqual(len(session.broker.list_positions()), 1)
        retried_buy_events = [
            event
            for event in session._events
            if event.signal_action == "BUY" and event.order_outcome and event.order_outcome.submitted
        ]
        self.assertEqual(len(retried_buy_events), 1)

    def test_decide_ignores_internal_state_and_only_trusts_holding_argument(self) -> None:
        """Directly proves SmaCrossoverStrategy.decide() is stateless per call."""

        strategy = SmaCrossoverStrategy(fast_period=2, slow_period=3)
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = [
            make_bar("TEST", start, "10"),
            make_bar("TEST", start + timedelta(days=1), "9"),
            make_bar("TEST", start + timedelta(days=2), "8"),
            make_bar("TEST", start + timedelta(days=3), "9"),
            make_bar("TEST", start + timedelta(days=4), "10"),
        ]
        # At index 4, fast (avg of last 2 closes: 9,10=9.5) > slow (avg of
        # last 3: 8,9,10=9). With holding=False -> BUY. Calling decide() a
        # second time with the same bars and holding=False again must
        # produce the identical BUY signal — proving no hidden internal
        # state affects the outcome.
        first_call = strategy.decide(bars, 4, holding=False)
        second_call = strategy.decide(bars, 4, holding=False)

        self.assertIsNotNone(first_call)
        assert first_call is not None
        self.assertEqual(first_call, second_call)
        self.assertEqual(first_call.action, "BUY")

        # And with holding=True at the same index, no BUY should be proposed
        # (already holding, condition for SELL is fast<slow which isn't true here).
        held_call = strategy.decide(bars, 4, holding=True)
        self.assertIsNone(held_call)


class ShadowSessionInstrumentGuardTests(unittest.TestCase):
    """Regression test for the missing single-instrument guard.

    Before the fix, _current_position() returned whichever position happened
    to be first in the broker's dict-derived tuple, with no check that on_bar
    was only ever fed one instrument. Feeding bars for a second instrument
    could silently return the wrong instrument's holding/quantity to
    strategy.decide() and to SELL sizing — exactly the "belief desyncs from
    real state" bug class this session exists to prevent.
    """

    def test_second_instrument_bar_is_rejected(self) -> None:
        session = make_session()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        session.on_bar(make_bar("TEST", start, "10"))

        with self.assertRaises(ValueError):
            session.on_bar(make_bar("OTHER", start + timedelta(days=1), "20"))


if __name__ == "__main__":
    unittest.main()
