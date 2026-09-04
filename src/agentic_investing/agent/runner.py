"""Scheduled Claude-driven agent loop: the "pre-market analysis" runner.

This is the ONE place in the codebase where an actual LLM call happens. Its
job ends at calling ``AgentToolkit`` methods (read-only research/journal
tools, plus the single risk-gated ``submit_trade_proposal``) — it has no
other capability, matching the standing "agents propose, code decides"
invariant enforced structurally by ``ProposalExecutor``.

Anthropic's Claude was chosen as the reasoning model: strong tool-use
support, and MCP (the protocol used by ``agentic_investing.mcp_server``) is
Anthropic's own open standard, so the same toolkit naturally works from
either this custom runner or a generic MCP client such as Claude Desktop.

The client is accessed through a small ``AnthropicClient`` Protocol rather
than importing the SDK type directly, so tests can inject a scripted fake
message sequence with no network access and no API key — mirroring the
``http_get``-injection pattern already used by ``AlphaVantageClient``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol, cast

from .tools import AgentToolkit, build_anthropic_tool_schemas

DEFAULT_MODEL = "claude-sonnet-4-5"

SYSTEM_PROMPT = (
    "You are a cautious equity research analyst for an Indian-markets (NSE/BSE) stock "
    "trading account. Your ONLY job is to research one instrument at a time using the "
    "tools provided, then call submit_trade_proposal exactly once with your decision "
    "(BUY, SELL, or HOLD) and your reasoning.\n\n"
    "Non-negotiable priorities, in order:\n"
    "1. Reduce risk. If you are not genuinely confident in a BUY, propose HOLD.\n"
    "2. A monthly return of roughly 2% is a soft, non-mandatory aspiration. NEVER "
    "recommend a trade you would not otherwise recommend just to chase this number, "
    "and never treat missing it as a failure.\n\n"
    "Ground rules:\n"
    "- Always check get_journal_history and get_daily_plan FIRST, so you do not "
    "duplicate or contradict a very recent decision on the same instrument.\n"
    "- Use get_recent_bars, get_news_sentiment, get_company_overview, and "
    "get_technical_indicator as needed to form a view grounded in both technicals and "
    "fundamentals/news, not vibes.\n"
    "- You cannot place an order directly. submit_trade_proposal is independently "
    "risk-checked by deterministic code after you call it — it may reject your proposal "
    "regardless of your confidence, and any target/stop you suggest is only honored if "
    "at least as conservative as the platform's own default. This is intentional.\n"
    "- Call submit_trade_proposal exactly once per instrument per run, as your final action."
)


class AnthropicMessage(Protocol):
    """The minimal shape of an Anthropic ``Message`` response used here."""

    content: list[Any]
    stop_reason: str | None


class AnthropicClient(Protocol):
    """The minimal Anthropic SDK surface this runner depends on."""

    def create_message(
        self, *, model: str, max_tokens: int, system: str, messages: list[dict[str, Any]], tools: tuple[dict[str, Any], ...]
    ) -> AnthropicMessage: ...


class RealAnthropicClient:
    """Thin adapter over ``anthropic.Anthropic`` matching the ``AnthropicClient`` protocol."""

    def __init__(self, *, api_key: str | None = None) -> None:
        import anthropic

        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("Anthropic API key not provided. Pass api_key= or set ANTHROPIC_API_KEY.")
        self._client = anthropic.Anthropic(api_key=resolved_key)

    def create_message(
        self, *, model: str, max_tokens: int, system: str, messages: list[dict[str, Any]], tools: tuple[dict[str, Any], ...]
    ) -> AnthropicMessage:
        # The real SDK's TypedDict-based request/response types are far more
        # specific than the small structural Protocol this runner depends
        # on (AnthropicMessage/AnthropicClient) — that Protocol is the
        # deliberately narrow surface tests fake against. Casting here keeps
        # the SDK's own strict typing from leaking into the rest of this
        # module while still calling the real, correctly-typed SDK method.
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=cast(Any, messages),
            tools=cast(Any, list(tools)),
        )
        return cast(AnthropicMessage, response)


@dataclass(frozen=True, slots=True)
class AgentRunConfig:
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    max_tool_iterations: int = 12


@dataclass(frozen=True, slots=True)
class InstrumentRunResult:
    """What happened for one instrument during one runner pass."""

    instrument: str
    exchange: str
    tool_calls: tuple[str, ...]  # names of every tool the model invoked, in order
    final_text: str  # the model's closing summary, for the operator report
    proposal_submitted: bool  # did submit_trade_proposal get called at all


class AgentRunner:
    """Runs the Claude tool-use loop for a watchlist, one instrument at a time.

    Deliberately processes ONE instrument per conversation: keeps each run
    scoped and auditable (the journal records exactly which instrument each
    decision was about), and avoids one instrument's context/tool results
    crowding out another's within Claude's context window.
    """

    def __init__(self, *, toolkit: AgentToolkit, client: AnthropicClient, config: AgentRunConfig | None = None) -> None:
        self.toolkit = toolkit
        self.client = client
        self.config = config or AgentRunConfig()
        # Derived once from the toolkit's own method signatures — see
        # build_anthropic_tool_schemas()'s docstring for why this is never
        # hand-maintained. Recomputed per-instance (not module-level) so a
        # toolkit with extra/custom tools would be reflected automatically.
        self._tool_schemas = build_anthropic_tool_schemas(self.toolkit)

    def _dispatch_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        method = getattr(self.toolkit, name, None)
        if method is None:
            return {"error": f"unknown tool: {name}"}
        try:
            return method(**tool_input)
        except Exception as exc:  # noqa: BLE001 — surfaced to the model as a tool error, not a crash
            return {"error": str(exc)}

    def run_for_instrument(self, *, instrument: str, exchange: str = "NSE") -> InstrumentRunResult:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Analyze {instrument} on {exchange} and decide whether to BUY, SELL, or "
                    "HOLD, then call submit_trade_proposal with your decision."
                ),
            }
        ]
        tool_calls: list[str] = []
        proposal_submitted = False
        final_text = ""

        for _ in range(self.config.max_tool_iterations):
            response = self.client.create_message(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=self._tool_schemas,
            )

            assistant_content = [_block_to_param(block) for block in response.content]
            messages.append({"role": "assistant", "content": assistant_content})

            tool_use_blocks = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            text_blocks = [block for block in response.content if getattr(block, "type", None) == "text"]
            if text_blocks:
                final_text = text_blocks[-1].text

            if not tool_use_blocks:
                break

            tool_results = []
            for block in tool_use_blocks:
                tool_calls.append(block.name)
                if block.name == "submit_trade_proposal":
                    proposal_submitted = True
                result = self._dispatch_tool(block.name, dict(block.input))
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

            if response.stop_reason != "tool_use":
                break

        return InstrumentRunResult(
            instrument=instrument.upper(),
            exchange=exchange.upper(),
            tool_calls=tuple(tool_calls),
            final_text=final_text,
            proposal_submitted=proposal_submitted,
        )

    def run_for_watchlist(self, watchlist: list[tuple[str, str]]) -> tuple[InstrumentRunResult, ...]:
        return tuple(self.run_for_instrument(instrument=instrument, exchange=exchange) for instrument, exchange in watchlist)


def _block_to_param(block: Any) -> dict[str, Any]:
    """Convert an SDK content block back into the plain-dict form the API expects on resend."""

    block_type = getattr(block, "type", None)
    if block_type == "text":
        return {"type": "text", "text": block.text}
    if block_type == "tool_use":
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": dict(block.input)}
    raise ValueError(f"unsupported content block type: {block_type!r}")
