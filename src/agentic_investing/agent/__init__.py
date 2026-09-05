"""Controlled agentic reasoning layer (roadmap Phase 7).

Only the reasoning step here is "the agent" (an LLM call, in
``runner.py``). Everything else — the ``TradeProposal`` schema,
``ProposalExecutor``'s risk gating, the trade journal — is plain,
deterministic, tested Python, matching the platform's standing principle:
agents propose, code decides.

No component in this package is permitted to call a broker method directly;
``ProposalExecutor`` is the only path from a proposal to ``OrderManager``,
and it always consults the same ``RiskEngine`` every other execution path
in this project uses.
"""

from .executor import ProposalExecutor, ProposalExecutorConfig, ProposalResult
from .proposal import ProposalAction, TradeProposal
from .providers import ModelClient, OpenAICompatibleModelClient, create_model_client
from .runner import AgentRunConfig, AgentRunner, AnthropicClient, InstrumentRunResult, RealAnthropicClient
from .tools import AgentToolkit

__all__ = [
    "AgentRunConfig",
    "AgentRunner",
    "AgentToolkit",
    "AnthropicClient",
    "InstrumentRunResult",
    "ModelClient",
    "OpenAICompatibleModelClient",
    "ProposalAction",
    "ProposalExecutor",
    "ProposalExecutorConfig",
    "ProposalResult",
    "RealAnthropicClient",
    "create_model_client",
    "TradeProposal",
]
