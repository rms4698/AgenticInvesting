import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from mcp.server.mcpserver import MCPServer

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.agent.tools import TOOL_METHOD_NAMES, AgentToolkit
from agentic_investing.data.models import Bar
from agentic_investing.journal import TradeJournal


def make_toolkit(root: Path) -> AgentToolkit:
    data_dir = root / "data"
    data_dir.mkdir()
    dataset = data_dir / "nse_teststock_1d.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "instrument": "TESTSTOCK",
                    "exchange": "NSE",
                    "timeframe": "1d",
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "available_at": "2026-01-02T00:00:00+00:00",
                    "open": "100",
                    "high": "101",
                    "low": "99",
                    "close": "100.5",
                    "volume": 1000,
                }
            ]
        ),
        encoding="utf-8",
    )
    return AgentToolkit(journal=TradeJournal(root / "journal.sqlite3"), data_dir=data_dir)


class DynamicMcpToolTests(unittest.TestCase):
    def test_every_registered_tool_is_listed_with_a_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            toolkit = make_toolkit(Path(temp_dir))
            server = MCPServer(name="test-agentic-investing")
            for name in TOOL_METHOD_NAMES:
                server.add_tool(getattr(toolkit, name), name=name)

            tools = asyncio.run(server.list_tools())
            self.assertEqual({tool.name for tool in tools}, set(TOOL_METHOD_NAMES))
            self.assertTrue(all(tool.input_schema["type"] == "object" for tool in tools))
            toolkit.close()

    def test_all_registered_read_and_action_tools_execute_through_mcp(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            toolkit = make_toolkit(Path(temp_dir))
            server = MCPServer(name="test-agentic-investing")
            for name in TOOL_METHOD_NAMES:
                server.add_tool(getattr(toolkit, name), name=name)

            async def call_tools() -> dict[str, object]:
                recent = await server.call_tool(
                    "get_recent_bars", {"instrument": "TESTSTOCK", "exchange": "NSE", "count": 1}
                )
                history = await server.call_tool(
                    "get_journal_history", {"instrument": "TESTSTOCK", "exchange": "NSE"}
                )
                plan = await server.call_tool(
                    "get_daily_plan", {"instrument": "TESTSTOCK", "exchange": "NSE"}
                )
                proposal = await server.call_tool(
                    "submit_trade_proposal",
                    {
                        "instrument": "TESTSTOCK",
                        "exchange": "NSE",
                        "action": "HOLD",
                        "reasoning": "MCP tool wiring test; no trade intended",
                    },
                )
                return {"recent": recent, "history": history, "plan": plan, "proposal": proposal}

            results = asyncio.run(call_tools())
            self.assertFalse(results["recent"].is_error)  # type: ignore[union-attr]
            self.assertFalse(results["history"].is_error)  # type: ignore[union-attr]
            self.assertFalse(results["plan"].is_error)  # type: ignore[union-attr]
            self.assertFalse(results["proposal"].is_error)  # type: ignore[union-attr]
            toolkit.close()


if __name__ == "__main__":
    unittest.main()
