"""Risk-gated execution of agent-produced trade proposals.

This is the safety boundary described in ``proposal.py``: an agent's
``TradeProposal`` can only ever reach a broker through this class, and this
class never places a BUY without first passing ``RiskEngine`` — exactly the
same invariant ``OrderManager`` already enforces for the SMA strategy.
Nothing here is an "agent" in the LLM sense; it is plain, deterministic,
testable Python, matching the "not everything is agent" principle: the
agent's job ends at producing a ``TradeProposal``, and everything from here
on is code, not reasoning.

Independent stop-loss/target enforcement, mirroring
``ShadowTradingSession``: once a BUY fills, this executor tracks the
resulting stop/target levels itself and checks them against every
subsequent bar's intrabar high/low — it does not trust the agent to
"remember" to exit; exits are enforced by code every single call.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from agentic_investing.data.models import Bar
from agentic_investing.execution import OrderManager, OrderOutcome, PaperBroker
from agentic_investing.journal import TradeJournal
from agentic_investing.risk import RiskEngine, RiskLimits

from .proposal import TradeProposal


@dataclass(frozen=True, slots=True)
class ProposalExecutorConfig:
    initial_capital: Decimal = Decimal("100000")
    commission_rate: Decimal = Decimal("0.0003")
    slippage_rate: Decimal = Decimal("0.0005")
    stop_distance_fraction: Decimal = Decimal("0.05")  # sizing only, see RiskEngine
    stop_loss_distance_fraction: Decimal = Decimal("0.20")  # actual stop trigger distance
    enable_stop_loss: bool = True
    enable_target_exit: bool = True
    # Soft aspiration only — NEVER a forced quota. The executor does not
    # change behavior based on this value; it exists purely so reporting
    # can note progress toward it without pretending it is a requirement.
    monthly_return_aspiration_fraction: Decimal = Decimal("0.02")

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.commission_rate < 0 or self.slippage_rate < 0:
            raise ValueError("cost rates cannot be negative")
        if self.stop_distance_fraction <= 0 or self.stop_distance_fraction >= 1:
            raise ValueError("stop_distance_fraction must be between 0 and 1")
        if self.stop_loss_distance_fraction <= 0 or self.stop_loss_distance_fraction >= 1:
            raise ValueError("stop_loss_distance_fraction must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class ProposalResult:
    """What actually happened after a proposal was risk-checked."""

    proposal: TradeProposal
    outcome: OrderOutcome | None  # None for a HOLD or a no-op SELL with no position
    approved: bool
    reasons: tuple[str, ...] = ()


class ProposalExecutor:
    """The only path from an agent's ``TradeProposal`` to a broker call.

    One instance manages exactly one instrument at a time (matching
    ``ShadowTradingSession``'s existing single-instrument invariant); run
    one executor per instrument in the daily driver's loop over a watchlist.
    """

    def __init__(
        self,
        *,
        instrument: str,
        exchange: str,
        config: ProposalExecutorConfig | None = None,
        risk_limits: RiskLimits | None = None,
        journal: TradeJournal | None = None,
        broker: PaperBroker | None = None,
        risk_engine: RiskEngine | None = None,
    ) -> None:
        self.instrument = instrument
        self.exchange = exchange
        self.config = config or ProposalExecutorConfig()
        self.risk_limits = risk_limits or RiskLimits(account_capital=self.config.initial_capital)
        if self.risk_limits.account_capital != self.config.initial_capital:
            raise ValueError("risk limits capital must match executor initial capital")

        self.broker = broker or PaperBroker(self.config.initial_capital, self.config.commission_rate)
        self.risk_engine = risk_engine or RiskEngine(self.risk_limits)
        self.order_manager = OrderManager(self.broker, self.risk_engine)
        self.journal = journal or TradeJournal()

        self._stop_price: Decimal | None = None
        self._target_price: Decimal | None = None

    def _current_position(self):
        positions = self.broker.list_positions()
        return positions[0] if positions else None

    def mark_to_market(self, bar: Bar) -> None:
        """Update the risk engine's equity tracking for this bar, and check
        any active stop-loss/target BEFORE evaluating a new proposal.

        Call this once per bar prior to ``execute``. Stop/target checks here
        mirror ``ShadowTradingSession``: they run every bar a position is
        held, independent of whether the agent proposes anything at all —
        an agent that goes silent, errors out, or simply doesn't run that
        day must never leave a position with no enforced downside cap.
        """

        if (bar.instrument, bar.exchange) != (self.instrument, self.exchange):
            raise ValueError(
                f"ProposalExecutor for {self.exchange}:{self.instrument} received a bar for "
                f"{bar.exchange}:{bar.instrument}"
            )

        position = self._current_position()
        equity = self.broker.cash_balance() + (position.quantity * bar.open if position is not None else Decimal("0"))
        self.risk_engine.mark_to_market(equity, bar.timestamp)

        if position is None:
            return

        stop_hit = self.config.enable_stop_loss and self._stop_price is not None and bar.low <= self._stop_price
        target_hit = (
            self.config.enable_target_exit and self._target_price is not None and bar.high >= self._target_price
        )
        if stop_hit:
            trigger_price = min(bar.open, self._stop_price)  # type: ignore[arg-type]
            fill_price = trigger_price * (Decimal("1") - self.config.slippage_rate)
            outcome = self.order_manager.submit_sell(
                client_order_id=f"{self.instrument}-{bar.timestamp.isoformat()}-stoploss",
                instrument=self.instrument,
                exchange=self.exchange,
                quantity=position.quantity,
                fill_price=fill_price,
                timestamp=bar.timestamp,
            )
            self.journal.add_entry(
                category="OUTCOME",
                instrument=self.instrument,
                exchange=self.exchange,
                message=f"Stop-loss triggered at {fill_price:.2f}",
                data={"submitted": outcome.submitted, "reasons": list(outcome.reasons)},
                timestamp=bar.timestamp,
            )
            self._stop_price = None
            self._target_price = None
        elif target_hit:
            trigger_price = max(bar.open, self._target_price)  # type: ignore[arg-type]
            fill_price = trigger_price * (Decimal("1") - self.config.slippage_rate)
            outcome = self.order_manager.submit_sell(
                client_order_id=f"{self.instrument}-{bar.timestamp.isoformat()}-target",
                instrument=self.instrument,
                exchange=self.exchange,
                quantity=position.quantity,
                fill_price=fill_price,
                timestamp=bar.timestamp,
            )
            self.journal.add_entry(
                category="OUTCOME",
                instrument=self.instrument,
                exchange=self.exchange,
                message=f"Profit target reached at {fill_price:.2f}",
                data={"submitted": outcome.submitted, "reasons": list(outcome.reasons)},
                timestamp=bar.timestamp,
            )
            self._stop_price = None
            self._target_price = None

    def execute(self, proposal: TradeProposal, *, bar: Bar) -> ProposalResult:
        """Risk-gate and (if approved) execute one agent proposal.

        ``bar`` supplies the fill-price basis (this bar's open, matching the
        next-bar-open convention used everywhere else in this platform) and
        must be the same bar most recently passed to ``mark_to_market``.
        """

        if (proposal.instrument, proposal.exchange) != (self.instrument, self.exchange):
            raise ValueError("proposal instrument/exchange does not match this executor")

        self.journal.add_entry(
            category="DECISION",
            instrument=proposal.instrument,
            exchange=proposal.exchange,
            message=f"Agent proposed {proposal.action} (confidence={proposal.confidence:.2f}): {proposal.reasoning}",
            data={
                "action": proposal.action,
                "confidence": proposal.confidence,
                "target_price": str(proposal.target_price) if proposal.target_price else None,
                "stop_price": str(proposal.stop_price) if proposal.stop_price else None,
                "sources": list(proposal.sources),
            },
            timestamp=bar.timestamp,
        )

        position = self._current_position()

        if proposal.action == "HOLD":
            return ProposalResult(proposal=proposal, outcome=None, approved=True)

        if proposal.action == "BUY":
            if position is not None:
                return ProposalResult(
                    proposal=proposal, outcome=None, approved=False, reasons=("already holding a position",)
                )
            equity = self.broker.cash_balance()
            fill_price = bar.open * (Decimal("1") + self.config.slippage_rate)
            outcome = self.order_manager.submit_buy(
                client_order_id=f"{self.instrument}-{bar.timestamp.isoformat()}-buy",
                instrument=self.instrument,
                exchange=self.exchange,
                equity=equity,
                fill_price=fill_price,
                stop_distance_fraction=self.config.stop_distance_fraction,
                initial_capital=self.config.initial_capital,
                commission_rate=self.config.commission_rate,
                timestamp=bar.timestamp,
            )
            if outcome.submitted:
                # The agent's suggested target/stop are advisory inputs, not
                # trusted directly: they are only used if they fall on the
                # safe side of what RiskEngine's own distance would produce,
                # otherwise the deterministic default wins. This prevents an
                # agent from ever widening its own stop past the configured
                # risk tolerance.
                default_stop = fill_price * (Decimal("1") - self.config.stop_loss_distance_fraction)
                default_target = fill_price * (
                    Decimal("1") + self.config.stop_loss_distance_fraction * self.risk_limits.minimum_reward_risk
                )
                self._stop_price = (
                    max(proposal.stop_price, default_stop)
                    if proposal.stop_price is not None
                    else default_stop
                ) if self.config.enable_stop_loss else None
                self._target_price = (
                    min(proposal.target_price, default_target)
                    if proposal.target_price is not None
                    else default_target
                ) if self.config.enable_target_exit else None
                self.journal.set_daily_plan(
                    instrument=self.instrument,
                    exchange=self.exchange,
                    thesis=proposal.reasoning,
                    target_price=str(self._target_price) if self._target_price else None,
                    stop_price=str(self._stop_price) if self._stop_price else None,
                    data={"confidence": proposal.confidence, "sources": list(proposal.sources)},
                    timestamp=bar.timestamp,
                )
            else:
                self.journal.add_entry(
                    category="OUTCOME",
                    instrument=self.instrument,
                    exchange=self.exchange,
                    message=f"BUY blocked: {'; '.join(outcome.reasons) or 'unknown reason'}",
                    data={"reasons": list(outcome.reasons)},
                    timestamp=bar.timestamp,
                )
            return ProposalResult(proposal=proposal, outcome=outcome, approved=outcome.submitted, reasons=outcome.reasons)

        if proposal.action == "SELL":
            if position is None:
                return ProposalResult(
                    proposal=proposal, outcome=None, approved=False, reasons=("no open position to sell",)
                )
            fill_price = bar.open * (Decimal("1") - self.config.slippage_rate)
            outcome = self.order_manager.submit_sell(
                client_order_id=f"{self.instrument}-{bar.timestamp.isoformat()}-sell",
                instrument=self.instrument,
                exchange=self.exchange,
                quantity=position.quantity,
                fill_price=fill_price,
                timestamp=bar.timestamp,
            )
            self._stop_price = None
            self._target_price = None
            return ProposalResult(proposal=proposal, outcome=outcome, approved=outcome.submitted, reasons=outcome.reasons)

        raise AssertionError(f"unreachable: unknown action {proposal.action!r}")
