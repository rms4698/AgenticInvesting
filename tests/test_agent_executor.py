import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.agent import ProposalExecutor, ProposalExecutorConfig, TradeProposal
from agentic_investing.data.models import Bar
from agentic_investing.journal import TradeJournal
from agentic_investing.risk import RiskLimits


def make_bar(
    instrument: str,
    timestamp: datetime,
    *,
    open_: str,
    high: str | None = None,
    low: str | None = None,
    close: str | None = None,
    exchange: str = "NSE",
) -> Bar:
    open_price = Decimal(open_)
    high_price = Decimal(high) if high else open_price
    low_price = Decimal(low) if low else open_price
    close_price = Decimal(close) if close else open_price
    return Bar(
        instrument,
        exchange,
        "1d",
        timestamp,
        timestamp,
        open_price,
        high_price,
        low_price,
        close_price,
        100000,
    )


def make_executor(**config_overrides) -> ProposalExecutor:
    temp_dir = tempfile.mkdtemp()
    journal_path = Path(temp_dir) / "journal.sqlite3"
    config = ProposalExecutorConfig(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
        **config_overrides,
    )
    return ProposalExecutor(
        instrument="TESTSTOCK",
        exchange="NSE",
        config=config,
        journal=TradeJournal(journal_path),
    )


class ProposalRiskGatingTests(unittest.TestCase):
    """Regression tests proving the agent can never bypass RiskEngine.

    These mirror OrderManager's own gating tests: a HOLD/BUY/SELL proposal
    is only ever a *request*; RiskEngine and PaperBroker's own invariants
    (no shorting, no double-buying, insufficient cash) are always the final
    word, regardless of what the proposal claims.
    """

    def test_buy_proposal_is_approved_and_position_opens(self) -> None:
        executor = make_executor()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar)

        proposal = TradeProposal(
            instrument="TESTSTOCK",
            exchange="NSE",
            action="BUY",
            reasoning="RSI oversold, positive earnings surprise",
            confidence=0.7,
        )
        result = executor.execute(proposal, bar=bar)

        self.assertTrue(result.approved)
        self.assertIsNotNone(executor._current_position())

    def test_buy_proposal_rejected_when_already_holding(self) -> None:
        executor = make_executor()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar1 = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar1)
        executor.execute(
            TradeProposal(instrument="TESTSTOCK", exchange="NSE", action="BUY", reasoning="initial buy"),
            bar=bar1,
        )

        bar2 = make_bar("TESTSTOCK", start + timedelta(days=1), open_="101")
        executor.mark_to_market(bar2)
        result = executor.execute(
            TradeProposal(instrument="TESTSTOCK", exchange="NSE", action="BUY", reasoning="buy more"),
            bar=bar2,
        )

        self.assertFalse(result.approved)
        self.assertIn("already holding a position", result.reasons)

    def test_sell_proposal_rejected_when_no_position(self) -> None:
        executor = make_executor()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar)

        result = executor.execute(
            TradeProposal(instrument="TESTSTOCK", exchange="NSE", action="SELL", reasoning="take profit"),
            bar=bar,
        )

        self.assertFalse(result.approved)
        self.assertIn("no open position to sell", result.reasons)

    def test_buy_proposal_blocked_by_kill_switch_regardless_of_confidence(self) -> None:
        """A high-confidence agent proposal must still be blocked by a tripped kill switch."""

        aggressive_limits = RiskLimits(
            account_capital=Decimal("100000"),
            risk_per_trade_fraction=Decimal("0.5"),
            max_open_portfolio_risk_fraction=Decimal("0.5"),
            max_single_position_fraction=Decimal("0.5"),
            capital_deployment_fraction=Decimal("1.0"),
        )
        temp_dir = tempfile.mkdtemp()
        executor = ProposalExecutor(
            instrument="TESTSTOCK",
            exchange="NSE",
            config=ProposalExecutorConfig(
                initial_capital=Decimal("100000"), commission_rate=Decimal("0"), slippage_rate=Decimal("0")
            ),
            risk_limits=aggressive_limits,
            journal=TradeJournal(Path(temp_dir) / "journal.sqlite3"),
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar1 = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar1)
        executor.execute(
            TradeProposal(instrument="TESTSTOCK", exchange="NSE", action="BUY", reasoning="entry", confidence=0.99),
            bar=bar1,
        )

        # Crash the price to trip the hard-drawdown kill switch, then sell to flatten.
        bar_crash = make_bar("TESTSTOCK", start + timedelta(days=1), open_="1")
        executor.mark_to_market(bar_crash)
        executor.execute(
            TradeProposal(instrument="TESTSTOCK", exchange="NSE", action="SELL", reasoning="stop out"),
            bar=bar_crash,
        )
        self.assertTrue(executor.risk_engine.kill_switch_triggered)

        bar_recover = make_bar("TESTSTOCK", start + timedelta(days=2), open_="5")
        executor.mark_to_market(bar_recover)
        result = executor.execute(
            TradeProposal(
                instrument="TESTSTOCK",
                exchange="NSE",
                action="BUY",
                reasoning="I am extremely confident this will recover",
                confidence=0.99,
            ),
            bar=bar_recover,
        )

        self.assertFalse(result.approved)
        self.assertIsNone(executor._current_position())


class ProposalStopLossAndTargetTests(unittest.TestCase):
    """Regression tests proving independent stop-loss/target enforcement.

    An agent's suggested stop/target is only ever used if it is at least as
    conservative as the deterministic default — it can never widen its own
    downside protection past the configured risk tolerance.
    """

    def test_default_stop_and_target_applied_when_agent_omits_them(self) -> None:
        executor = make_executor(stop_loss_distance_fraction=Decimal("0.10"))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar)
        executor.execute(
            TradeProposal(instrument="TESTSTOCK", exchange="NSE", action="BUY", reasoning="entry"),
            bar=bar,
        )

        self.assertEqual(executor._stop_price, Decimal("90"))  # 100 * (1 - 0.10)

    def test_agent_cannot_widen_stop_past_deterministic_default(self) -> None:
        """A proposal suggesting a looser (lower) stop than the default must be ignored."""

        executor = make_executor(stop_loss_distance_fraction=Decimal("0.10"))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar)
        executor.execute(
            TradeProposal(
                instrument="TESTSTOCK",
                exchange="NSE",
                action="BUY",
                reasoning="entry",
                stop_price=Decimal("50"),  # much looser than the 90 default
            ),
            bar=bar,
        )

        # The tighter (higher) of the two stops wins: max(50, 90) = 90.
        self.assertEqual(executor._stop_price, Decimal("90"))

    def test_agent_can_tighten_stop_below_deterministic_default(self) -> None:
        """A proposal suggesting a tighter (higher) stop than the default IS honored."""

        executor = make_executor(stop_loss_distance_fraction=Decimal("0.10"))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar)
        executor.execute(
            TradeProposal(
                instrument="TESTSTOCK",
                exchange="NSE",
                action="BUY",
                reasoning="entry",
                stop_price=Decimal("95"),  # tighter than the 90 default
            ),
            bar=bar,
        )

        self.assertEqual(executor._stop_price, Decimal("95"))

    def test_stop_loss_fires_intrabar_even_without_a_new_proposal(self) -> None:
        """mark_to_market alone (no execute call) must still enforce the stop."""

        executor = make_executor(stop_loss_distance_fraction=Decimal("0.10"))
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar1 = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar1)
        executor.execute(
            TradeProposal(instrument="TESTSTOCK", exchange="NSE", action="BUY", reasoning="entry"),
            bar=bar1,
        )
        self.assertIsNotNone(executor._current_position())

        # No proposal at all on this bar — the agent could be offline,
        # erroring, or simply not have run. The stop must still fire.
        bar_crash = make_bar("TESTSTOCK", start + timedelta(days=1), open_="92", high="93", low="85", close="88")
        executor.mark_to_market(bar_crash)

        self.assertIsNone(executor._current_position())
        stop_entries = [
            entry
            for entry in executor.journal.recent_entries(instrument="TESTSTOCK", exchange="NSE")
            if "Stop-loss" in entry.message
        ]
        self.assertEqual(len(stop_entries), 1)


class ProposalJournalTests(unittest.TestCase):
    def test_every_proposal_is_logged_to_the_journal(self) -> None:
        executor = make_executor()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bar = make_bar("TESTSTOCK", start, open_="100")
        executor.mark_to_market(bar)
        executor.execute(
            TradeProposal(
                instrument="TESTSTOCK",
                exchange="NSE",
                action="HOLD",
                reasoning="no clear signal either way",
                sources=("technical:RSI", "news:none"),
            ),
            bar=bar,
        )

        entries = executor.journal.recent_entries(instrument="TESTSTOCK", exchange="NSE")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].category, "DECISION")
        self.assertIn("HOLD", entries[0].message)


if __name__ == "__main__":
    unittest.main()
