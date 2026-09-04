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


if __name__ == "__main__":
    unittest.main()
