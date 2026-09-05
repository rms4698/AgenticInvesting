"""Plain-function tool implementations shared by the MCP server and the
Claude-driven agent runner.

Extracted so the exact same read-only research tools and the one risk-gated
``submit_trade_proposal`` action are available to (a) any generic MCP client
(Claude Desktop, etc., via ``agentic_investing.mcp_server``) and (b) the
purpose-built scheduled loop in ``agentic_investing.agent.runner`` — without
duplicating the tool logic (and therefore the risk-safety guarantees) in two
places.

Design note — schemas are derived, never hand-maintained: every public
method below is a plain, fully type-annotated function. Both consumers
(``mcp_server.server``, via ``MCPServer.add_tool()``, and ``agent.runner``,
via ``build_anthropic_tool_schemas()`` in this module) generate their JSON
Schemas directly from these method signatures using Pydantic/MCP's own
introspection (``mcp.server.mcpserver.utilities.func_metadata``). Adding,
removing, or changing the signature of a tool method here is therefore the
ONLY place a change is ever needed — there is no separate, hand-written
schema to keep in sync, and no risk of the two "views" (generic MCP client
vs. the custom Claude runner) silently drifting apart.

Every function here is plain, deterministic, testable Python; none of them
is "the agent." ``AgentToolkit.submit_trade_proposal`` is still the only
function in this module that can affect a broker position, and it still
only ever does so via ``ProposalExecutor``.
"""

from __future__ import annotations

import inspect
import os
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from mcp.server.mcpserver.utilities.func_metadata import func_metadata

from agentic_investing.agent.executor import ProposalExecutor, ProposalExecutorConfig, ProposalResult
from agentic_investing.agent.proposal import TradeProposal
from agentic_investing.data.json_loader import load_bars_json
from agentic_investing.data.models import Bar
from agentic_investing.journal import TradeJournal
from agentic_investing.logging_config import get_logger

# The single source of truth for "which AgentToolkit methods are tools."
# Both the MCP server and the Anthropic-facing schema builder iterate this
# list — add a method name here once, and it becomes available through
# every consumer with a schema derived automatically from its signature.
TOOL_METHOD_NAMES: tuple[str, ...] = (
    "get_recent_bars",
    "get_journal_history",
    "get_daily_plan",
    "submit_trade_proposal",
)


def default_data_dir() -> Path:
    return Path(os.environ.get("AGENTIC_INVESTING_DATA_DIR", "data/real"))


def local_dataset_path(instrument: str, exchange: str, timeframe: str, *, data_dir: Path | None = None) -> Path:
    base = data_dir if data_dir is not None else default_data_dir()
    return base / f"{exchange.lower()}_{instrument.lower()}_{timeframe}.json"


def load_local_bars(instrument: str, exchange: str, timeframe: str, *, data_dir: Path | None = None) -> list[Bar]:
    path = local_dataset_path(instrument, exchange, timeframe, data_dir=data_dir)
    if not path.exists():
        raise FileNotFoundError(
            f"No local dataset at {path}. Ingest it first with scripts/fetch_kite_history.py "
            "or scripts/run_daily_shadow_update.py."
        )
    return load_bars_json(path)


class AgentToolkit:
    """Bundles the shared research/journal/execution tools behind one object.

    ``executor_factory`` is injectable purely for testing; production callers
    can leave it at its default.
    """

    def __init__(
        self,
        *,
        journal: TradeJournal | None = None,
        data_dir: Path | None = None,
        executor_config: ProposalExecutorConfig | None = None,
        executor_factory: Callable[[str, str, ProposalExecutorConfig, TradeJournal], ProposalExecutor] | None = None,
    ) -> None:
        self.journal = journal or TradeJournal()
        self._logger = get_logger(__name__)
        self.data_dir = data_dir
        self._executor_config = executor_config or ProposalExecutorConfig()
        self._executor_factory = executor_factory or (
            lambda instrument, exchange, config, journal: ProposalExecutor(
                instrument=instrument, exchange=exchange, config=config, journal=journal
            )
        )
        self._executors: dict[tuple[str, str], ProposalExecutor] = {}
        self._logger.info("agent_toolkit_ready tools=%s", ",".join(TOOL_METHOD_NAMES))

    def _get_executor(self, instrument: str, exchange: str) -> ProposalExecutor:
        key = (instrument.upper(), exchange.upper())
        if key not in self._executors:
            self._executors[key] = self._executor_factory(key[0], key[1], self._executor_config, self.journal)
        return self._executors[key]

    def close(self) -> None:
        """Close resources owned by the toolkit, including journal storage."""

        self.journal.close()

    def get_recent_bars(
        self, *, instrument: str, exchange: str = "NSE", timeframe: str = "1d", count: int = 60
    ) -> list[dict[str, Any]]:
        """Return the most recent OHLCV bars already ingested locally for this instrument.

        This does NOT call Kite live — it reads the local dataset produced by
        scripts/fetch_kite_history.py / ingest_historical_bars, keeping this
        tool read-only and safe to call as often as needed.
        """

        path = local_dataset_path(instrument, exchange, timeframe, data_dir=self.data_dir)
        bars = load_local_bars(instrument, exchange, timeframe, data_dir=self.data_dir)
        self._logger.debug(
            "loading_local_bars instrument=%s exchange=%s timeframe=%s path=%s",
            instrument,
            exchange,
            timeframe,
            path,
        )
        recent = bars[-count:]
        return [
            {
                "timestamp": bar.timestamp.isoformat(),
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
            }
            for bar in recent
        ]

    def get_journal_history(
        self, *, instrument: str | None = None, exchange: str = "NSE", limit: int = 20
    ) -> list[dict[str, Any]]:
        """Recent trade-journal entries: past analyses, decisions, and outcomes.

        Always check this before proposing a new trade — it is this platform's
        memory across runs, and will tell you what was already decided, why,
        and what happened.
        """

        entries = self.journal.recent_entries(
            instrument=instrument, exchange=exchange if instrument else None, limit=limit
        )
        return [
            {
                "timestamp": entry.timestamp.isoformat(),
                "instrument": entry.instrument,
                "exchange": entry.exchange,
                "category": entry.category,
                "message": entry.message,
                "data": entry.data,
            }
            for entry in entries
        ]

    def get_daily_plan(self, *, instrument: str, exchange: str = "NSE") -> dict[str, Any]:
        """The current plan (thesis, target, stop) recorded for this instrument, if any."""

        plan = self.journal.get_daily_plan(instrument=instrument, exchange=exchange)
        if plan is None:
            return {"error": "no plan recorded for this instrument"}
        return {
            "instrument": plan.instrument,
            "exchange": plan.exchange,
            "updated_at": plan.updated_at.isoformat(),
            "thesis": plan.thesis,
            "target_price": plan.target_price,
            "stop_price": plan.stop_price,
            "data": plan.data,
        }

    def submit_trade_proposal(
        self,
        *,
        instrument: str,
        action: str,
        reasoning: str,
        exchange: str = "NSE",
        confidence: float = 0.5,
        target_price: str | None = None,
        stop_price: str | None = None,
        sources: list[str] | None = None,
    ) -> dict[str, Any]:
        """The ONLY way to act on a decision. Always independently risk-checked; may be rejected.

        ``action`` must be BUY, SELL, or HOLD. ``reasoning`` must explain the
        decision in plain language — it is written to the trade journal. This
        tool uses the most recent locally ingested bar as the fill-price basis;
        call get_recent_bars first so the proposal reflects current price levels.
        Your suggested target_price/stop_price are advisory only: they are only
        honored if at least as conservative as this platform's own deterministic
        risk defaults, and are otherwise silently overridden. This function is the
        ONLY function in this toolkit that can affect a broker position.
        """

        bars = load_local_bars(instrument, exchange, "1d", data_dir=self.data_dir)
        if not bars:
            return {"error": "no local bars available to use as a fill-price basis"}
        latest_bar = bars[-1]

        proposal = TradeProposal(
            instrument=instrument.upper(),
            exchange=exchange.upper(),
            action=action.upper(),  # type: ignore[arg-type]
            reasoning=reasoning,
            confidence=confidence,
            target_price=Decimal(target_price) if target_price else None,
            stop_price=Decimal(stop_price) if stop_price else None,
            sources=tuple(sources or ()),
        )

        executor = self._get_executor(instrument, exchange)
        executor.mark_to_market(latest_bar)
        result: ProposalResult = executor.execute(proposal, bar=latest_bar)

        return {
            "approved": result.approved,
            "reasons": list(result.reasons),
            "action": proposal.action,
            "order_submitted": result.outcome.submitted if result.outcome else None,
        }


def build_anthropic_tool_schemas(toolkit: "AgentToolkit") -> tuple[dict[str, Any], ...]:
    """Derive Anthropic tool-use schemas directly from ``AgentToolkit``'s methods.

    Uses the exact same signature-introspection machinery the MCP server
    already relies on (``mcp`` package's ``func_metadata``), so the two
    consumers can never drift: change a method's parameters once, in one
    place, and both the generic MCP surface and the Claude-facing schema
    picked up here update automatically. Never hand-maintain a parallel
    schema list again.
    """

    schemas: list[dict[str, Any]] = []
    for name in TOOL_METHOD_NAMES:
        method = getattr(toolkit, name)
        metadata = func_metadata(method)
        input_schema = metadata.arg_model.model_json_schema()
        # Drop the pydantic-generated "title" — Anthropic's schema does not
        # need it and it is redundant with the tool name.
        input_schema.pop("title", None)
        schemas.append(
            {
                "name": name,
                "description": (inspect.getdoc(method) or "").strip() or name,
                "input_schema": input_schema,
            }
        )
    return tuple(schemas)
