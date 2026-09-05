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

Research tooling uses Claude's native ``web_search`` server tool (see
``WEB_SEARCH_TOOL_TYPE``) for Indian-specific news, company filings,
fundamentals, corporate actions, and financial press. It lets the model read
moneycontrol.com, screener.in, NSE/BSE circulars, and other sources directly,
without this application scraping unofficial endpoints or depending on a
poorly documented market-data aggregator. ``web_search`` is a pure "server
tool" — Anthropic executes it and returns results already embedded in the
response, so it never goes through ``AgentToolkit``/``ProposalExecutor`` and
cannot affect a broker position.

The client is accessed through a small ``AnthropicClient`` Protocol rather
than importing the SDK type directly, so tests can inject a scripted fake
message sequence with no network access and no API key.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from typing import Any

from agentic_investing.config import load_prompt
from agentic_investing.logging_config import get_logger

from .providers import AnthropicModelClient, ModelClient, NormalizedMessage
from .tools import AgentToolkit, build_anthropic_tool_schemas

DEFAULT_MODEL = "claude-sonnet-4-5"

# Anthropic's server-executed web search tool. Versioned per Anthropic's own
# tool-versioning scheme (not this project's); see the "Web search tool"
# section of Anthropic's docs for newer versions (e.g. dynamic filtering).
# This is a SERVER tool: Anthropic runs the search itself and returns
# results inline in the response — it is never dispatched through
# ``AgentToolkit._dispatch_tool`` and has no path to a broker call.
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
SYSTEM_PROMPT_FILE = "agent_system.md"
TASK_PROMPT_FILE = "agent_task.md"


AnthropicMessage = NormalizedMessage
AnthropicClient = ModelClient
RealAnthropicClient = AnthropicModelClient


@dataclass(frozen=True, slots=True)
class AgentRunConfig:
    model: str = DEFAULT_MODEL
    max_tokens: int = 4096
    max_tool_iterations: int = 12
    # Claude's native web_search server tool — see the module docstring for
    # why this is on by default for India-specific research. Anthropic bills per search
    # ($10/1,000 as of writing) in addition to token costs, so max_web_searches
    # caps the damage from a misbehaving/looping model within one instrument.
    enable_web_search: bool = True
    max_web_searches: int = 5


@dataclass(frozen=True, slots=True)
class InstrumentRunResult:
    """What happened for one instrument during one runner pass."""

    instrument: str
    exchange: str
    tool_calls: tuple[str, ...]  # names of every CLIENT tool the model invoked, in order
    final_text: str  # the model's closing summary, for the operator report
    proposal_submitted: bool  # did submit_trade_proposal get called at all
    web_search_count: int = 0  # number of web_search server-tool calls (Anthropic bills per search)


class AgentRunner:
    """Runs the Claude tool-use loop for a watchlist, one instrument at a time.

    Deliberately processes ONE instrument per conversation: keeps each run
    scoped and auditable (the journal records exactly which instrument each
    decision was about), and avoids one instrument's context/tool results
    crowding out another's within Claude's context window.
    """

    def __init__(self, *, toolkit: AgentToolkit, client: ModelClient, config: AgentRunConfig | None = None) -> None:
        self.toolkit = toolkit
        self.client = client
        self.config = config or AgentRunConfig()
        self._logger = get_logger(__name__)
        research_instructions = (
            "Use the native web-search tool for current Indian news, company announcements, "
            "financial results, corporate actions, and fundamentals. Prefer authoritative "
            "sources such as NSE/BSE notices, company filings, investor-relations pages, "
            "and established Indian financial publications. Search multiple independent "
            "sources when the information could materially affect a trade. Treat search "
            "results as evidence requiring source and date checking."
            if self.config.enable_web_search
            else "Current web research is unavailable for this provider. Do not invent current news or fundamentals; if current information is needed but unavailable, prefer HOLD."
        )
        self._system_prompt = load_prompt(SYSTEM_PROMPT_FILE).format(
            research_instructions=research_instructions
        )
        # Derived once from the toolkit's own method signatures — see
        # build_anthropic_tool_schemas()'s docstring for why this is never
        # hand-maintained. Recomputed per-instance (not module-level) so a
        # toolkit with extra/custom tools would be reflected automatically.
        # Claude's server-executed web_search tool is appended alongside the
        # dynamically-derived client tools when enabled (see module
        # docstring for why this is on by default for Indian-market
        # research) — it never goes through AgentToolkit._dispatch_tool.
        tool_schemas = build_anthropic_tool_schemas(self.toolkit)
        if self.config.enable_web_search:
            web_search_tool: dict[str, Any] = {
                "type": WEB_SEARCH_TOOL_TYPE,
                "name": "web_search",
                "max_uses": self.config.max_web_searches,
            }
            tool_schemas = tool_schemas + (web_search_tool,)
        self._tool_schemas = tool_schemas

    def _dispatch_tool(self, name: str, tool_input: dict[str, Any]) -> Any:
        method = getattr(self.toolkit, name, None)
        if method is None:
            self._logger.error("unknown_client_tool name=%s", name)
            return {"error": f"unknown tool: {name}"}
        self._logger.info("client_tool_call name=%s", name)
        try:
            result = method(**tool_input)
            self._logger.info("client_tool_result name=%s", name)
            return result
        except Exception as exc:  # noqa: BLE001 — surfaced to the model as a tool error, not a crash
            self._logger.exception("client_tool_failed name=%s", name)
            return {"error": str(exc)}

    def run_for_instrument(self, *, instrument: str, exchange: str = "NSE") -> InstrumentRunResult:
        task_prompt = load_prompt(TASK_PROMPT_FILE).format(instrument=instrument, exchange=exchange)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": task_prompt,
            }
        ]
        tool_calls: list[str] = []
        proposal_submitted = False
        web_search_count = 0
        final_text = ""

        for iteration in range(self.config.max_tool_iterations):
            self._logger.debug(
                "agent_turn instrument=%s exchange=%s iteration=%d",
                instrument,
                exchange,
                iteration,
            )
            response = self.client.create_message(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                system=self._system_prompt,
                messages=messages,
                tools=self._tool_schemas,
            )

            assistant_content = [_block_to_param(block) for block in response.content]
            messages.append({"role": "assistant", "content": assistant_content})

            # Client (dispatched-through-AgentToolkit) tool calls only.
            # Claude's server-executed web_search calls show up as
            # "server_tool_use"/"web_search_tool_result" blocks instead —
            # Anthropic runs those itself and they never reach
            # _dispatch_tool, so they are counted separately below and
            # excluded from tool_use_blocks entirely.
            tool_use_blocks = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
            text_blocks = [block for block in response.content if getattr(block, "type", None) == "text"]
            web_search_count += sum(
                1
                for block in response.content
                if getattr(block, "type", None) == "server_tool_use"
                and getattr(block, "name", None) == "web_search"
            )
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
            web_search_count=web_search_count,
        )

    def run_for_watchlist(self, watchlist: list[tuple[str, str]]) -> tuple[InstrumentRunResult, ...]:
        return tuple(self.run_for_instrument(instrument=instrument, exchange=exchange) for instrument, exchange in watchlist)


def _block_to_param(block: Any) -> dict[str, Any]:
    """Convert an SDK content block back into the plain-dict form the API expects on resend.

    Deliberately generic rather than enumerating each block type by name:
    besides "text" and "tool_use", Anthropic's server tools (e.g. web_search)
    produce "server_tool_use" and "web_search_tool_result" blocks that must
    be echoed back byte-for-byte — including opaque fields like
    ``encrypted_content``/``encrypted_index`` — for multi-turn continuity;
    Anthropic rejects a request if those fields are missing or modified.
    Hand-listing every current and future block type here would both violate
    that byte-for-byte requirement (easy to typo/drop a field) and need
    updating every time Anthropic adds a new tool/block type. Real SDK
    objects are pydantic models (``model_dump``); test fakes are plain
    dataclasses (``dataclasses.asdict``) — both are handled without knowing
    the specific block type in advance.
    """

    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    if dataclasses.is_dataclass(block) and not isinstance(block, type):
        return dataclasses.asdict(block)
    block_type = getattr(block, "type", None)
    if block_type is None:
        raise ValueError(f"cannot convert content block without a 'type': {block!r}")
    return {"type": block_type, **{k: v for k, v in vars(block).items() if not k.startswith("_")}}
