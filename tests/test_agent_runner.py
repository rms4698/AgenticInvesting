import sys
import tempfile
import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.agent import AgentRunConfig, AgentRunner, AgentToolkit
from agentic_investing.data.models import Bar
from agentic_investing.journal import TradeJournal
from agentic_investing.research import AlphaVantageClient


@dataclass
class FakeTextBlock:
    text: str
    type: str = "text"


@dataclass
class FakeToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class FakeServerToolUseBlock:
    """Mimics Claude's web_search server-tool-use content block."""

    id: str
    input: dict[str, Any]
    name: str = "web_search"
    type: str = "server_tool_use"


@dataclass
class FakeWebSearchResultBlock:
    """Mimics Claude's web_search_tool_result content block.

    Real blocks carry opaque fields (e.g. per-result ``encrypted_content``)
    that must round-trip unchanged; this fake keeps a representative subset
    to prove _block_to_param handles an unrecognized, not-hardcoded block
    type generically rather than only "text"/"tool_use".
    """

    tool_use_id: str
    content: list[dict[str, Any]]
    type: str = "web_search_tool_result"


@dataclass
class FakeMessage:
    content: list[Any]
    stop_reason: str | None


class ScriptedAnthropicClient:
    """Replays a fixed sequence of ``FakeMessage``s, ignoring the actual request content.

    This is the injectable fake matching the ``AnthropicClient`` Protocol —
    no network access, no API key, fully deterministic.
    """

    def __init__(self, script: list[FakeMessage]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def create_message(self, *, model, max_tokens, system, messages, tools) -> FakeMessage:
        self.calls.append({"model": model, "messages": [dict(message) for message in messages], "tools": tools})
        if not self._script:
            raise AssertionError("ScriptedAnthropicClient script exhausted — test sent more turns than scripted")
        return self._script.pop(0)


def make_toolkit_with_bars(tmp_path: Path) -> AgentToolkit:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dataset_path = data_dir / "nse_teststock_1d.json"
    bar = Bar(
        instrument="TESTSTOCK",
        exchange="NSE",
        timeframe="1d",
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        available_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.5"),
        volume=1000,
    )
    import json

    payload = [
        {
            "instrument": bar.instrument,
            "exchange": bar.exchange,
            "timeframe": bar.timeframe,
            "timestamp": bar.timestamp.isoformat(),
            "available_at": bar.available_at.isoformat(),
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": bar.volume,
        }
    ]
    dataset_path.write_text(json.dumps(payload), encoding="utf-8")

    journal = TradeJournal(tmp_path / "journal.sqlite3")
    return AgentToolkit(
        journal=journal,
        data_dir=data_dir,
        alpha_vantage_client_factory=lambda: AlphaVantageClient(api_key="unused", http_get=lambda *a, **k: {}),
    )


class AgentRunnerToolLoopTests(unittest.TestCase):
    def test_runner_dispatches_tool_calls_and_submits_a_proposal(self) -> None:
        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))

        script = [
            FakeMessage(
                content=[
                    FakeToolUseBlock(id="call_1", name="get_journal_history", input={"instrument": "TESTSTOCK"}),
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="call_2",
                        name="submit_trade_proposal",
                        input={
                            "instrument": "TESTSTOCK",
                            "action": "BUY",
                            "reasoning": "positive momentum, no conflicting recent journal entry",
                            "confidence": 0.7,
                        },
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(
                content=[FakeTextBlock(text="Proposed a BUY based on momentum; risk engine approved it.")],
                stop_reason="end_turn",
            ),
        ]
        client = ScriptedAnthropicClient(script)
        runner = AgentRunner(toolkit=toolkit, client=client, config=AgentRunConfig(max_tool_iterations=5))

        result = runner.run_for_instrument(instrument="TESTSTOCK", exchange="NSE")

        self.assertEqual(result.tool_calls, ("get_journal_history", "submit_trade_proposal"))
        self.assertTrue(result.proposal_submitted)
        self.assertIn("risk engine approved", result.final_text)

        # The proposal should have actually been journaled by the real toolkit/executor.
        entries = toolkit.get_journal_history(instrument="TESTSTOCK", exchange="NSE")
        self.assertTrue(any("BUY" in entry["message"] for entry in entries))

    def test_runner_stops_immediately_if_model_never_calls_a_tool(self) -> None:
        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        script = [FakeMessage(content=[FakeTextBlock(text="Nothing interesting today.")], stop_reason="end_turn")]
        client = ScriptedAnthropicClient(script)
        runner = AgentRunner(toolkit=toolkit, client=client)

        result = runner.run_for_instrument(instrument="TESTSTOCK", exchange="NSE")

        self.assertEqual(result.tool_calls, ())
        self.assertFalse(result.proposal_submitted)
        self.assertEqual(result.final_text, "Nothing interesting today.")

    def test_runner_respects_max_tool_iterations_without_crashing(self) -> None:
        """A model that never stops calling tools must not loop forever."""

        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        # 10 identical tool-use turns, all requesting get_daily_plan.
        script = [
            FakeMessage(
                content=[FakeToolUseBlock(id=f"call_{i}", name="get_daily_plan", input={"instrument": "TESTSTOCK"})],
                stop_reason="tool_use",
            )
            for i in range(10)
        ]
        client = ScriptedAnthropicClient(script)
        runner = AgentRunner(toolkit=toolkit, client=client, config=AgentRunConfig(max_tool_iterations=3))

        result = runner.run_for_instrument(instrument="TESTSTOCK", exchange="NSE")

        self.assertEqual(len(result.tool_calls), 3)
        self.assertFalse(result.proposal_submitted)

    def test_unknown_tool_name_returns_an_error_result_without_crashing(self) -> None:
        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        script = [
            FakeMessage(
                content=[FakeToolUseBlock(id="call_1", name="not_a_real_tool", input={})],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeTextBlock(text="ok")], stop_reason="end_turn"),
        ]
        client = ScriptedAnthropicClient(script)
        runner = AgentRunner(toolkit=toolkit, client=client)

        result = runner.run_for_instrument(instrument="TESTSTOCK", exchange="NSE")

        self.assertEqual(result.tool_calls, ("not_a_real_tool",))

    def test_run_for_watchlist_runs_each_instrument_independently(self) -> None:
        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        # Same script replayed per instrument since ScriptedAnthropicClient
        # is shared; use two short one-turn scripts concatenated.
        script = [
            FakeMessage(content=[FakeTextBlock(text="First instrument done.")], stop_reason="end_turn"),
            FakeMessage(content=[FakeTextBlock(text="Second instrument done.")], stop_reason="end_turn"),
        ]
        client = ScriptedAnthropicClient(script)
        runner = AgentRunner(toolkit=toolkit, client=client)

        results = runner.run_for_watchlist([("TESTSTOCK", "NSE"), ("TESTSTOCK", "NSE")])

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].final_text, "First instrument done.")
        self.assertEqual(results[1].final_text, "Second instrument done.")


class AgentRunnerWebSearchTests(unittest.TestCase):
    """Regression tests for Claude's native web_search server tool.

    Covers: (1) the tool schema is included by default and can be disabled,
    (2) server-tool-use/result blocks are counted separately from client
    tool_use blocks and never dispatched through AgentToolkit, and (3) an
    arbitrary/unrecognized server-tool content block still round-trips via
    _block_to_param without needing a hardcoded branch for its type.
    """

    def test_web_search_tool_schema_included_by_default(self) -> None:
        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        client = ScriptedAnthropicClient([FakeMessage(content=[FakeTextBlock(text="done")], stop_reason="end_turn")])
        runner = AgentRunner(toolkit=toolkit, client=client)

        tool_names = [tool["name"] for tool in runner._tool_schemas]
        self.assertIn("web_search", tool_names)
        web_search_schema = next(tool for tool in runner._tool_schemas if tool["name"] == "web_search")
        self.assertTrue(web_search_schema["type"].startswith("web_search_"))
        self.assertEqual(web_search_schema["max_uses"], AgentRunConfig().max_web_searches)

    def test_web_search_tool_omitted_when_disabled(self) -> None:
        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        client = ScriptedAnthropicClient([FakeMessage(content=[FakeTextBlock(text="done")], stop_reason="end_turn")])
        runner = AgentRunner(toolkit=toolkit, client=client, config=AgentRunConfig(enable_web_search=False))

        tool_names = [tool["name"] for tool in runner._tool_schemas]
        self.assertNotIn("web_search", tool_names)

    def test_server_tool_use_blocks_are_counted_and_never_dispatched(self) -> None:
        """A turn that mixes a server-tool (web_search) call with a client tool call.

        This is the realistic scenario that requires us to continue the
        conversation: Anthropic resolves web_search itself within the same
        response, but a client tool_use block still requires us to execute
        it and send a tool_result before Claude can produce its final text.
        """

        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        script = [
            FakeMessage(
                content=[
                    FakeServerToolUseBlock(id="srvtoolu_1", input={"query": "RELIANCE quarterly results"}),
                    FakeWebSearchResultBlock(
                        tool_use_id="srvtoolu_1",
                        content=[
                            {
                                "type": "web_search_result",
                                "url": "https://example.com/a",
                                "title": "Reliance Q3 results",
                                "encrypted_content": "opaque-blob-must-round-trip",
                            }
                        ],
                    ),
                    FakeToolUseBlock(id="call_1", name="get_daily_plan", input={"instrument": "TESTSTOCK"}),
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(
                content=[FakeTextBlock(text="Based on the search, results were strong.")],
                stop_reason="end_turn",
            ),
        ]
        client = ScriptedAnthropicClient(script)
        runner = AgentRunner(toolkit=toolkit, client=client)

        result = runner.run_for_instrument(instrument="TESTSTOCK", exchange="NSE")

        # web_search is a server tool: it must be counted, but must NOT
        # appear in tool_calls (that list is only for AgentToolkit-dispatched
        # client tools) and must never reach _dispatch_tool.
        self.assertEqual(result.web_search_count, 1)
        self.assertEqual(result.tool_calls, ("get_daily_plan",))
        self.assertEqual(result.final_text, "Based on the search, results were strong.")

        # The opaque encrypted_content must have round-tripped byte-for-byte
        # into the resent "assistant" message on the second call.
        second_call = client.calls[1]
        assistant_messages = [m for m in second_call["messages"] if m["role"] == "assistant"]
        result_block = next(
            block
            for block in assistant_messages[0]["content"]
            if block.get("type") == "web_search_tool_result"
        )
        self.assertEqual(result_block["content"][0]["encrypted_content"], "opaque-blob-must-round-trip")

    def test_web_search_disabled_still_handles_a_normal_run(self) -> None:
        """Disabling web_search must not break the ordinary client-tool flow."""

        toolkit = make_toolkit_with_bars(Path(tempfile.mkdtemp()))
        script = [
            FakeMessage(
                content=[
                    FakeToolUseBlock(
                        id="call_1",
                        name="submit_trade_proposal",
                        input={"instrument": "TESTSTOCK", "action": "HOLD", "reasoning": "no clear signal"},
                    )
                ],
                stop_reason="tool_use",
            ),
            FakeMessage(content=[FakeTextBlock(text="Held, no action taken.")], stop_reason="end_turn"),
        ]
        client = ScriptedAnthropicClient(script)
        runner = AgentRunner(toolkit=toolkit, client=client, config=AgentRunConfig(enable_web_search=False))

        result = runner.run_for_instrument(instrument="TESTSTOCK", exchange="NSE")

        self.assertEqual(result.tool_calls, ("submit_trade_proposal",))
        self.assertEqual(result.web_search_count, 0)
        self.assertTrue(result.proposal_submitted)


if __name__ == "__main__":
    unittest.main()
