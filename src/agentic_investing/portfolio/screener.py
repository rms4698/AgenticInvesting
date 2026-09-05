"""Deterministic stock screening and portfolio decision logic."""

from __future__ import annotations

from decimal import Decimal
from typing import Mapping, Sequence

from agentic_investing.data.models import Bar
from agentic_investing.features import calculate_technical_snapshot

from .models import FundamentalSnapshot, PortfolioDecision, ScreenedCandidate, ScreeningConfig


def screen_instrument(
    bars: Sequence[Bar],
    fundamentals: FundamentalSnapshot,
    index: int,
    *,
    config: ScreeningConfig | None = None,
) -> ScreenedCandidate | None:
    """Return a candidate only when all configured deterministic gates pass."""

    actual_config = config or ScreeningConfig()
    technical = calculate_technical_snapshot(bars, index)
    if technical is None:
        return None
    bar = bars[index]
    if fundamentals.available_at > bar.available_at:
        return None
    if fundamentals.market_cap is not None and actual_config.min_market_cap is not None:
        if fundamentals.market_cap < actual_config.min_market_cap:
            return None
    if fundamentals.pe_ratio is not None and actual_config.max_pe_ratio is not None:
        if fundamentals.pe_ratio > actual_config.max_pe_ratio:
            return None
    if fundamentals.revenue_growth is not None and actual_config.min_revenue_growth is not None:
        if fundamentals.revenue_growth < actual_config.min_revenue_growth:
            return None
    if fundamentals.return_on_equity is not None and actual_config.min_return_on_equity is not None:
        if fundamentals.return_on_equity < actual_config.min_return_on_equity:
            return None
    if fundamentals.debt_to_equity is not None and actual_config.max_debt_to_equity is not None:
        if fundamentals.debt_to_equity > actual_config.max_debt_to_equity:
            return None
    average_volume = sum((item.volume for item in bars[max(0, index - 19) : index + 1]), 0) / min(index + 1, 20)
    if average_volume < actual_config.min_average_volume:
        return None
    if technical.sma_fast <= technical.sma_slow:
        return None
    if technical.atr <= 0:
        return None

    stop_price = technical.close - technical.atr * actual_config.stop_atr_multiple
    if stop_price <= 0:
        return None
    target_price = technical.close + (technical.close - stop_price) * actual_config.minimum_reward_risk
    score = _score(technical, fundamentals)
    reasons = (
        "fast SMA above slow SMA",
        f"RSI={technical.rsi:.2f}",
        f"volume ratio={technical.volume_ratio:.2f}",
        f"fundamentals source={fundamentals.source}",
    )
    return ScreenedCandidate(
        instrument=bar.instrument,
        exchange=bar.exchange,
        score=score,
        technical=technical,
        fundamentals=fundamentals,
        stop_price=stop_price,
        target_price=target_price,
        reasons=reasons,
    )


def build_portfolio_decisions(
    bars_by_instrument: Mapping[str, Sequence[Bar]],
    fundamentals_by_instrument: Mapping[str, FundamentalSnapshot],
    index: int,
    *,
    holdings: set[str] | None = None,
    config: ScreeningConfig | None = None,
) -> tuple[PortfolioDecision, ...]:
    """Rank candidates and emit BUY/SELL/HOLD decisions for one closed-bar index."""

    actual_config = config or ScreeningConfig()
    current_holdings = holdings or set()
    candidates: list[ScreenedCandidate] = []
    decisions: list[PortfolioDecision] = []
    for instrument, bars in bars_by_instrument.items():
        fundamentals = fundamentals_by_instrument.get(instrument)
        if fundamentals is None:
            decisions.append(PortfolioDecision(instrument, bars[0].exchange, "HOLD", None, ("missing fundamentals",)))
            continue
        candidate = screen_instrument(bars, fundamentals, index, config=actual_config)
        if candidate is not None:
            candidates.append(candidate)
        elif instrument in current_holdings:
            decisions.append(PortfolioDecision(instrument, bars[0].exchange, "SELL", None, ("screen no longer passes",)))

    ranked = sorted(candidates, key=lambda candidate: candidate.score, reverse=True)
    selected = {candidate.instrument for candidate in ranked[: actual_config.max_positions]}
    for candidate in ranked:
        if candidate.instrument in selected and candidate.instrument not in current_holdings:
            decisions.append(
                PortfolioDecision(candidate.instrument, candidate.exchange, "BUY", candidate, candidate.reasons)
            )
        elif candidate.instrument in current_holdings:
            decisions.append(
                PortfolioDecision(candidate.instrument, candidate.exchange, "HOLD", candidate, candidate.reasons)
            )
    return tuple(decisions)


def _score(technical, fundamentals: FundamentalSnapshot) -> Decimal:
    score = Decimal("0")
    score += min(technical.rsi / Decimal("100"), Decimal("1"))
    score += min(max(technical.volume_ratio, Decimal("0")), Decimal("3")) / Decimal("3")
    if fundamentals.revenue_growth is not None:
        score += max(fundamentals.revenue_growth, Decimal("0"))
    if fundamentals.return_on_equity is not None:
        score += max(fundamentals.return_on_equity, Decimal("0"))
    if fundamentals.debt_to_equity is not None:
        score -= min(fundamentals.debt_to_equity, Decimal("5")) / Decimal("5")
    return score
