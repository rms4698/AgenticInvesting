"""Deterministic, stateful risk gating for order sizing and kill-switch control.

This engine is intentionally independent of any strategy or LLM output. It is
used by the backtester today and is designed to gate live/paper order flow
identically in a later phase (same limits, same decisions, same kill switch).
"""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal

from agentic_investing.logging_config import get_logger

from .limits import RiskLimits


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """An explainable accept/reject outcome for a proposed new position."""

    approved: bool
    reasons: tuple[str, ...] = ()


class RiskEngine:
    """Tracks drawdown, daily/monthly loss, and a manually-resettable kill switch.

    All checks are deterministic and based only on account equity and the
    configured :class:`RiskLimits` — never on strategy text or LLM output.
    """

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self._logger = get_logger(__name__)
        self._peak_equity: Decimal | None = None
        self._current_equity: Decimal | None = None
        self._current_date: date | None = None
        self._current_month: tuple[int, int] | None = None
        self._daily_start_equity: Decimal | None = None
        self._monthly_start_equity: Decimal | None = None
        self._kill_switch_triggered = False
        self._kill_switch_reason: str | None = None
        self._reset_log: list[str] = []

    @property
    def kill_switch_triggered(self) -> bool:
        return self._kill_switch_triggered

    @property
    def kill_switch_reason(self) -> str | None:
        return self._kill_switch_reason

    @property
    def reset_log(self) -> tuple[str, ...]:
        """Audit trail of manual kill-switch resets, most recent last."""

        return tuple(self._reset_log)

    def mark_to_market(self, equity: Decimal, timestamp: datetime) -> None:
        """Update peak equity, day/month baselines, and trip the kill switch if breached.

        Must be called once per period (e.g. once per bar) with the account
        equity observed *before* any new order is considered for that period.
        """

        if equity < 0:
            raise ValueError("equity cannot be negative")
        day = timestamp.date()
        month = (timestamp.year, timestamp.month)

        self._current_equity = equity
        self._peak_equity = equity if self._peak_equity is None else max(self._peak_equity, equity)
        if self._current_date != day:
            self._current_date = day
            self._daily_start_equity = equity
        if self._current_month != month:
            self._current_month = month
            self._monthly_start_equity = equity

        if self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            if drawdown >= self.limits.hard_drawdown_fraction and not self._kill_switch_triggered:
                self._kill_switch_triggered = True
                self._kill_switch_reason = (
                    f"hard drawdown breached: {drawdown * 100:.2f}% >= "
                    f"{self.limits.hard_drawdown_fraction * 100:.2f}%"
                )
                self._logger.error("kill_switch_triggered reason=%s", self._kill_switch_reason)

    def reset_kill_switch(self, *, reason: str) -> None:
        """Manually clear a tripped kill switch. Requires an explicit reason.

        Re-baselines peak equity to the *current* equity so the switch does
        not immediately re-trip on the same drawdown level. This is a
        deliberate risk decision: after reset, further drawdown is measured
        from the depressed level at reset time, not from the original peak.
        The prior drawdown is not erased from history — it is recorded in
        ``reset_log`` — only the forward-looking trigger threshold moves.
        """

        if not reason.strip():
            raise ValueError("a non-empty reason is required to reset the kill switch")
        self._reset_log.append(reason.strip())
        self._kill_switch_triggered = False
        self._kill_switch_reason = None
        if self._current_equity is not None:
            self._peak_equity = self._current_equity
        self._logger.warning("kill_switch_reset reason=%s", reason.strip())

    def evaluate_new_position(self, *, equity: Decimal, open_position_count: int) -> RiskDecision:
        """Decide whether a new position may be opened at the given equity."""

        reasons: list[str] = []
        if self._kill_switch_triggered:
            reasons.append(f"kill switch active: {self._kill_switch_reason}")
        if self._daily_start_equity is not None:
            daily_loss = self._daily_start_equity - equity
            if daily_loss >= self.limits.daily_loss_limit:
                reasons.append(
                    f"daily loss limit breached: {daily_loss:.2f} >= {self.limits.daily_loss_limit:.2f}"
                )
        if self._monthly_start_equity is not None:
            monthly_loss = self._monthly_start_equity - equity
            if monthly_loss >= self.limits.monthly_loss_limit:
                reasons.append(
                    f"monthly loss limit breached: {monthly_loss:.2f} >= {self.limits.monthly_loss_limit:.2f}"
                )
        if open_position_count >= self.limits.max_positions:
            reasons.append(f"max open positions reached: {open_position_count} >= {self.limits.max_positions}")
        decision = RiskDecision(approved=not reasons, reasons=tuple(reasons))
        if not decision.approved:
            self._logger.warning("new_position_blocked reasons=%s", ";".join(decision.reasons))
        return decision

    def size_new_position(
        self,
        *,
        cash: Decimal,
        fill_price: Decimal,
        stop_distance_fraction: Decimal,
        initial_capital: Decimal,
        commission_rate: Decimal,
    ) -> int:
        """Return the largest quantity permitted by risk, value, and cash limits."""

        if fill_price <= 0:
            raise ValueError("fill_price must be positive")
        risk_budget = min(self.limits.risk_per_trade, self.limits.max_open_portfolio_risk)
        stop_distance = fill_price * stop_distance_fraction
        risk_quantity = int((risk_budget / stop_distance).to_integral_value(rounding=ROUND_DOWN))
        value_quantity = int(
            (self.limits.max_single_position_fraction * initial_capital / fill_price).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        deployment_quantity = int(
            (self.limits.max_deployed_capital / fill_price).to_integral_value(rounding=ROUND_DOWN)
        )
        cash_quantity = int(
            (cash / (fill_price * (Decimal("1") + commission_rate))).to_integral_value(rounding=ROUND_DOWN)
        )
        return max(0, min(risk_quantity, value_quantity, deployment_quantity, cash_quantity))

    def size_new_position_by_weight(
        self,
        *,
        cash: Decimal,
        fill_price: Decimal,
        initial_capital: Decimal,
        commission_rate: Decimal,
    ) -> int:
        """Return the largest quantity permitted by the per-position/deployment/cash caps only.

        Unlike :meth:`size_new_position`, this does NOT divide by a stop distance, so it does
        not shrink position size for volatile instruments with wide ATR-based stops. It is
        opt-in, used only by research backtests that want capital-weight-based sizing (bounded
        by the same ``max_single_position_fraction``/``max_deployed_capital`` caps) instead of
        risk-based sizing. Live/shadow/agent order flow must keep using ``size_new_position``.
        """

        if fill_price <= 0:
            raise ValueError("fill_price must be positive")
        value_quantity = int(
            (self.limits.max_single_position_fraction * initial_capital / fill_price).to_integral_value(
                rounding=ROUND_DOWN
            )
        )
        deployment_quantity = int(
            (self.limits.max_deployed_capital / fill_price).to_integral_value(rounding=ROUND_DOWN)
        )
        cash_quantity = int(
            (cash / (fill_price * (Decimal("1") + commission_rate))).to_integral_value(rounding=ROUND_DOWN)
        )
        return max(0, min(value_quantity, deployment_quantity, cash_quantity))
