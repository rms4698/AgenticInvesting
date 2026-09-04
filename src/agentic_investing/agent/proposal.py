"""Structured, schema-validated output of the agent's reasoning step.

This is the ONLY interface between "the agent" (an LLM call, or anything
else that reasons about what to do) and the rest of this platform. The
agent never calls a broker method directly; it can only produce a
``TradeProposal``, which ``ProposalExecutor`` then runs through the exact
same deterministic ``RiskEngine`` + ``OrderManager`` gate that the SMA
crossover strategy uses. This is what makes "the agent proposes, code
decides" a structural guarantee rather than a convention.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

ProposalAction = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True, slots=True)
class TradeProposal:
    """One instrument-level recommendation from the agent's reasoning step.

    ``target_price``/``stop_price`` are the agent's suggested profit target
    and stop-loss for a BUY — expressed as the actual price level, not a
    fraction, since the agent has seen the current price and can reason
    about support/resistance levels directly. ``ProposalExecutor`` converts
    these into the fractional distances ``RiskEngine`` expects and NEVER
    trusts them blindly: risk limits, position sizing, and the kill switch
    are still evaluated independently of anything the agent says.

    ``confidence`` and ``reasoning`` are logged to the trade journal for
    human review but never affect whether the proposal is approved — that
    is exclusively the risk engine's job.
    """

    instrument: str
    exchange: str
    action: ProposalAction
    reasoning: str
    confidence: float = 0.5  # 0..1, informational only
    target_price: Decimal | None = None  # required for BUY if enable_target_exit
    stop_price: Decimal | None = None  # required for BUY if enable_stop_loss
    sources: tuple[str, ...] = field(default_factory=tuple)  # e.g. "technical:RSI", "news:..."

    def __post_init__(self) -> None:
        if not self.instrument.strip():
            raise ValueError("instrument must not be empty")
        if not self.exchange.strip():
            raise ValueError("exchange must not be empty")
        if self.action not in ("BUY", "SELL", "HOLD"):
            raise ValueError(f"invalid action: {self.action!r}")
        if not self.reasoning.strip():
            raise ValueError("reasoning must not be empty — every proposal must be explainable")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.action == "BUY":
            if self.target_price is not None and self.target_price <= 0:
                raise ValueError("target_price must be positive")
            if self.stop_price is not None and self.stop_price <= 0:
                raise ValueError("stop_price must be positive")
