"""Shadow (live-data, simulated-fill) trading session for Phase 6.

Runs the same strategy, RiskEngine, and OrderManager/PaperBroker stack used in
backtesting against a live bar-by-bar feed, but never places real orders.
Detects data gaps/outages and suppresses new entries during them (exits are
never suppressed), and produces a daily operator report for manual review.

Position-aware signal generation: every trading decision is made via
``strategy.decide(bars, index, holding=...)``, where ``holding`` is read from
the real broker position (``PaperBroker.list_positions()``), not from any
internal state remembered by the strategy. This means a BUY that was
proposed but never actually filled (blocked by risk limits, insufficient
cash, a data outage, etc.) can never cause the strategy to believe it holds a
position it doesn't — the next bar's decision is always grounded in truth.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Sequence

from agentic_investing.data.models import Bar
from agentic_investing.execution import OrderManager, OrderOutcome, PaperBroker
from agentic_investing.risk import RiskEngine, RiskLimits
from agentic_investing.strategies import SmaCrossoverStrategy


@dataclass(frozen=True, slots=True)
class ShadowSessionConfig:
    initial_capital: Decimal = Decimal("100000")
    commission_rate: Decimal = Decimal("0.0003")
    slippage_rate: Decimal = Decimal("0.0005")
    # Used ONLY for position sizing (RiskEngine.size_new_position), not as
    # the actual stop-loss trigger distance — see stop_loss_distance_fraction.
    # Reusing this tight sizing fraction as the real exit distance caused the
    # stop to fire on ordinary day-to-day noise for a multi-week positional
    # strategy (verified on real NIFTYBEES data: 5% produced 126 whipsaw
    # trades and turned +9.68% into -2.95% return in the equivalent backtest).
    stop_distance_fraction: Decimal = Decimal("0.05")
    # The ACTUAL stop-loss/target trigger distance from entry price. Wider
    # than stop_distance_fraction on purpose — see BacktestConfig for the
    # full rationale and the walk-forward trade-off this default reflects;
    # kept identical between backtest and shadow session so results are
    # comparable.
    stop_loss_distance_fraction: Decimal = Decimal("0.20")
    max_bar_gap: timedelta = timedelta(days=4)  # tolerate weekends; flag longer gaps
    enable_stop_loss: bool = True
    enable_target_exit: bool = True

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.commission_rate < 0 or self.slippage_rate < 0:
            raise ValueError("cost rates cannot be negative")
        if self.stop_distance_fraction <= 0 or self.stop_distance_fraction >= 1:
            raise ValueError("stop_distance_fraction must be between 0 and 1")
        if self.stop_loss_distance_fraction <= 0 or self.stop_loss_distance_fraction >= 1:
            raise ValueError("stop_loss_distance_fraction must be between 0 and 1")
        if self.max_bar_gap <= timedelta(0):
            raise ValueError("max_bar_gap must be positive")


@dataclass(frozen=True, slots=True)
class Incident:
    """One noteworthy event for operator review; never resolved automatically."""

    timestamp: datetime
    category: str  # "DATA_GAP" | "ORDER_BLOCKED" | "MANUAL_STALE" | "STOP_LOSS" | "TARGET_EXIT"
    message: str


@dataclass(slots=True)
class _BarEvent:
    timestamp: datetime
    signal_action: str | None
    order_outcome: OrderOutcome | None
    suppressed_by_gap: bool


class ShadowTradingSession:
    """Bar-by-bar shadow trading using next-bar-open execution, like Backtester.

    Call :meth:`on_bar` once per incoming bar, in chronological order. New
    entries (BUY) are suppressed while data is stale (a gap larger than
    ``max_bar_gap``, or an explicit :meth:`mark_stale` call); exits (SELL) are
    never suppressed, matching the platform's risk-first invariant.
    """

    def __init__(
        self,
        *,
        strategy: SmaCrossoverStrategy,
        config: ShadowSessionConfig | None = None,
        risk_limits: RiskLimits | None = None,
    ) -> None:
        self.config = config or ShadowSessionConfig()
        self.risk_limits = risk_limits or RiskLimits(account_capital=self.config.initial_capital)
        if self.risk_limits.account_capital != self.config.initial_capital:
            raise ValueError("risk limits capital must match session initial capital")

        self.strategy = strategy
        self.broker = PaperBroker(self.config.initial_capital, self.config.commission_rate)
        self.risk_engine = RiskEngine(self.risk_limits)
        self.order_manager = OrderManager(self.broker, self.risk_engine)

        self._history: list[Bar] = []
        self._last_bar_timestamp: datetime | None = None
        self._explicit_stale: bool = False
        self._explicit_stale_reason: str | None = None
        self._events: list[_BarEvent] = []
        self.incidents: list[Incident] = []
        # Stop-loss/target levels for the currently open position, if any.
        # PaperBroker's Position model has no notion of these (it only knows
        # quantity/average_price), so this session tracks them itself — set
        # when a BUY fills, checked every subsequent bar, and cleared when
        # the position closes for any reason.
        self._stop_price: Decimal | None = None
        self._target_price: Decimal | None = None

    def mark_stale(self, reason: str) -> None:
        """Explicitly flag the feed as stale before a new bar arrives.

        Use this when an external monitor (heartbeat, token expiry, broker
        disconnect) detects a problem even though no bar gap has occurred
        yet. New entries are suppressed until the next :meth:`on_bar` call.
        """

        if not reason.strip():
            raise ValueError("a non-empty reason is required")
        self._explicit_stale = True
        self._explicit_stale_reason = reason.strip()
        self.incidents.append(Incident(datetime.now(timezone.utc), "MANUAL_STALE", reason.strip()))

    def on_bar(self, bar: Bar) -> None:
        """Process one new bar: detect gaps, mark to market, act on signals.

        Raises if fed a bar for a different instrument/exchange than any
        prior bar in this session. ``_current_position()`` assumes exactly
        one instrument may be held at a time; silently allowing a second
        instrument would let it return the wrong position (wrong quantity,
        wrong average price) for whichever instrument's bar happens to be
        processed, which is exactly the "belief desyncs from real broker
        state" bug class this session exists to avoid.
        """

        if self._history and (bar.instrument, bar.exchange) != (
            self._history[0].instrument,
            self._history[0].exchange,
        ):
            raise ValueError(
                "ShadowTradingSession supports exactly one instrument per session; "
                f"expected {self._history[0].exchange}:{self._history[0].instrument}, "
                f"got {bar.exchange}:{bar.instrument}"
            )

        gap_detected = False
        if self._last_bar_timestamp is not None:
            gap = bar.timestamp - self._last_bar_timestamp
            if gap > self.config.max_bar_gap:
                gap_detected = True
                self.incidents.append(
                    Incident(
                        bar.timestamp,
                        "DATA_GAP",
                        f"gap of {gap} between bars exceeds max_bar_gap={self.config.max_bar_gap}",
                    )
                )

        suppress_new_entries = gap_detected or self._explicit_stale
        self._explicit_stale = False  # fresh bar clears an explicit stale flag
        self._explicit_stale_reason = None

        self._history.append(bar)
        self._last_bar_timestamp = bar.timestamp

        position = self._current_position()
        equity_before_bar = self.broker.cash_balance() + (
            position.quantity * bar.open if position is not None else Decimal("0")
        )
        self.risk_engine.mark_to_market(equity_before_bar, bar.timestamp)

        outcome: OrderOutcome | None = None
        exit_action: str | None = None

        # Stop-loss and profit-target are checked every bar a position is
        # held, using THIS bar's intrabar low/high — not only on bars where
        # the strategy happens to emit a SELL signal. A lagging crossover
        # signal could otherwise let a large adverse move ride for days
        # before the strategy itself decides to exit. Exits here are never
        # suppressed by stale/gapped data, matching the platform's
        # risk-first invariant that closing a position is always permitted.
        # If both could be read as hit on the same bar (a large gap), the
        # stop takes priority — assume the worse outcome, not the better one.
        if position is not None and self._stop_price is not None and bar.low <= self._stop_price:
            trigger_price = min(bar.open, self._stop_price)
            fill_price = trigger_price * (Decimal("1") - self.config.slippage_rate)
            outcome = self.order_manager.submit_sell(
                client_order_id=f"{bar.instrument}-{bar.timestamp.isoformat()}-stoploss",
                instrument=bar.instrument,
                exchange=bar.exchange,
                quantity=position.quantity,
                fill_price=fill_price,
                timestamp=bar.timestamp,
            )
            exit_action = "STOP_LOSS"
            self.incidents.append(
                Incident(bar.timestamp, "STOP_LOSS", f"stop-loss triggered at {fill_price:.2f}")
            )
            self._stop_price = None
            self._target_price = None
        elif position is not None and self._target_price is not None and bar.high >= self._target_price:
            trigger_price = max(bar.open, self._target_price)
            fill_price = trigger_price * (Decimal("1") - self.config.slippage_rate)
            outcome = self.order_manager.submit_sell(
                client_order_id=f"{bar.instrument}-{bar.timestamp.isoformat()}-target",
                instrument=bar.instrument,
                exchange=bar.exchange,
                quantity=position.quantity,
                fill_price=fill_price,
                timestamp=bar.timestamp,
            )
            exit_action = "TARGET_EXIT"
            self.incidents.append(
                Incident(bar.timestamp, "TARGET_EXIT", f"profit target reached at {fill_price:.2f}")
            )
            self._stop_price = None
            self._target_price = None

        position = self._current_position()  # re-read: a stop/target exit above may have just closed it
        signal = self._pending_signal(holding=position is not None) if exit_action is None else None
        if signal is not None and signal.action == "BUY" and position is None:
            if suppress_new_entries:
                self.incidents.append(
                    Incident(bar.timestamp, "ORDER_BLOCKED", "BUY signal suppressed due to stale/gapped data")
                )
            else:
                fill_price = bar.open * (Decimal("1") + self.config.slippage_rate)
                outcome = self.order_manager.submit_buy(
                    client_order_id=f"{bar.instrument}-{bar.timestamp.isoformat()}-buy",
                    instrument=bar.instrument,
                    exchange=bar.exchange,
                    equity=equity_before_bar,
                    fill_price=fill_price,
                    stop_distance_fraction=self.config.stop_distance_fraction,
                    initial_capital=self.config.initial_capital,
                    commission_rate=self.config.commission_rate,
                    timestamp=bar.timestamp,
                )
                if outcome.submitted:
                    self._stop_price = (
                        fill_price * (Decimal("1") - self.config.stop_loss_distance_fraction)
                        if self.config.enable_stop_loss
                        else None
                    )
                    self._target_price = (
                        fill_price
                        * (
                            Decimal("1")
                            + self.config.stop_loss_distance_fraction * self.risk_limits.minimum_reward_risk
                        )
                        if self.config.enable_target_exit
                        else None
                    )
                else:
                    self.incidents.append(
                        Incident(bar.timestamp, "ORDER_BLOCKED", "; ".join(outcome.reasons) or "order blocked")
                    )
        elif signal is not None and signal.action == "SELL" and position is not None:
            # Exits are never suppressed by stale data or risk limits.
            fill_price = bar.open * (Decimal("1") - self.config.slippage_rate)
            outcome = self.order_manager.submit_sell(
                client_order_id=f"{bar.instrument}-{bar.timestamp.isoformat()}-sell",
                instrument=bar.instrument,
                exchange=bar.exchange,
                quantity=position.quantity,
                fill_price=fill_price,
                timestamp=bar.timestamp,
            )
            self._stop_price = None
            self._target_price = None

        self._events.append(
            _BarEvent(
                timestamp=bar.timestamp,
                signal_action=exit_action or (signal.action if signal else None),
                order_outcome=outcome,
                suppressed_by_gap=suppress_new_entries and signal is not None and signal.action == "BUY",
            )
        )

    def _pending_signal(self, *, holding: bool):
        """Return the signal decided on the second-to-last (closed) bar, if any.

        Mirrors Backtester: a signal decided on a closed bar executes at the
        *next* bar's open. ``holding`` must be the real broker position at
        this point, so the strategy is always grounded in what actually
        happened rather than an internally remembered belief that could
        desync from reality (e.g. a BUY that was proposed but blocked never
        causes a later, genuine buying opportunity to be missed).
        """

        if len(self._history) < 2:
            return None
        previous_index = len(self._history) - 2
        return self.strategy.decide(self._history, previous_index, holding=holding)

    def _current_position(self):
        positions = self.broker.list_positions()
        return positions[0] if positions else None

    def daily_report(self) -> str:
        """Render a deterministic Markdown operator summary for archiving."""

        position = self._current_position()
        cash = self.broker.cash_balance()
        equity = cash + (position.quantity * self._history[-1].close if position and self._history else Decimal("0"))
        submitted = sum(1 for event in self._events if event.order_outcome and event.order_outcome.submitted)
        blocked = sum(1 for event in self._events if event.order_outcome and not event.order_outcome.submitted)

        lines = [
            "# Shadow Trading Daily Report",
            "",
            f"- Bars processed: {len(self._history)}",
            f"- Last bar timestamp: {self._history[-1].timestamp.isoformat() if self._history else 'n/a'}",
            f"- Cash: {cash:.2f}",
            f"- Open position: {f'{position.quantity} @ {position.average_price:.2f}' if position else 'none'}",
            f"- Stop-loss level: {f'{self._stop_price:.2f}' if position and self._stop_price is not None else 'n/a'}",
            f"- Target level: {f'{self._target_price:.2f}' if position and self._target_price is not None else 'n/a'}",
            f"- Equity (mark-to-close): {equity:.2f}",
            f"- Orders submitted: {submitted}",
            f"- Orders blocked: {blocked}",
            f"- Kill switch: {'TRIPPED — ' + (self.risk_engine.kill_switch_reason or '') if self.risk_engine.kill_switch_triggered else 'clear'}",
            "",
            "## Incidents",
            "",
        ]
        if self.incidents:
            lines.append("| Timestamp | Category | Message |")
            lines.append("|---|---|---|")
            for incident in self.incidents:
                lines.append(f"| {incident.timestamp} | {incident.category} | {incident.message} |")
        else:
            lines.append("None.")
        lines.extend(["", "This report is a shadow-trading artifact; no live capital is involved."])
        return "\n".join(lines) + "\n"
