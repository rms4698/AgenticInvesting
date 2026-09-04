"""MCP tool server exposing this platform's capabilities to an agent.

Design invariant, restated from ``agentic_investing.agent``: the ONLY tool
in this server that can affect a broker position is ``submit_trade_proposal``,
and it does not place orders directly — it constructs a ``TradeProposal``
and hands it to ``ProposalExecutor``, which re-applies ``RiskEngine`` in
full. There is deliberately no ``place_order`` tool, no raw broker access,
and no way for a connected agent (Claude Desktop, or any other MCP client)
to bypass the risk gate. This is why we did not adopt Zerodha's own
``kite-mcp-server`` wholesale: that server exposes ``place_order`` directly
with no risk-engine step in between.

This module is a thin MCP-protocol adapter over ``agentic_investing.agent.
tools.AgentToolkit`` — the actual tool logic (and its risk-safety guarantees)
lives there and is shared with the Claude-driven scheduled runner in
``agentic_investing.agent.runner``, so there is exactly one implementation
of "what submit_trade_proposal actually does."

Tool registration is fully dynamic: every method named in
``agentic_investing.agent.tools.TOOL_METHOD_NAMES`` is registered via
``MCPServer.add_tool()``, which derives the tool's JSON Schema directly from
that method's type-annotated signature (via the same Pydantic-based
introspection the ``mcp`` package uses internally). There is no hand-written
schema anywhere in this file — adding a new tool to ``AgentToolkit`` and
listing its name in ``TOOL_METHOD_NAMES`` is the only change needed for it
to appear here (and, automatically, in the Claude-facing schema used by
``agentic_investing.agent.runner``).

Run with: ``python -m agentic_investing.mcp_server`` (stdio transport, the
default MCP client integration mode for tools like Claude Desktop).
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from agentic_investing.agent.tools import TOOL_METHOD_NAMES, AgentToolkit

server = MCPServer(
    name="agentic-investing",
    instructions=(
        "Tools for researching and proposing trades in Indian-market stocks. "
        "You may read market data, news, fundamentals, technical indicators, "
        "and this account's trade journal freely. You may NEVER place an "
        "order directly — the only way to act on a decision is "
        "submit_trade_proposal, which is independently risk-checked and may "
        "be rejected regardless of your confidence. Risk reduction is the "
        "first priority; a monthly return of about 2% is a soft aspiration, "
        "never a requirement to force a trade toward."
    ),
)

# Kept for the process lifetime so stop-loss/target state, the risk engine's
# equity curve, and the journal connection persist across tool calls within
# a single run.
_toolkit = AgentToolkit()

for _tool_name in TOOL_METHOD_NAMES:
    server.add_tool(getattr(_toolkit, _tool_name), name=_tool_name)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()

