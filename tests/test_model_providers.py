import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.agent.providers import (
    OpenAICompatibleModelClient,
    _to_openai_function_tool,
    _to_openai_messages,
    create_model_client,
)


class ProviderSchemaTests(unittest.TestCase):
    def test_shared_schema_converts_to_openai_function_schema(self) -> None:
        converted = _to_openai_function_tool(
            {
                "name": "submit_trade_proposal",
                "description": "Submit a risk-gated proposal",
                "input_schema": {"type": "object", "properties": {"action": {"type": "string"}}},
            }
        )
        self.assertEqual(converted["type"], "function")
        self.assertEqual(converted["function"]["name"], "submit_trade_proposal")
        self.assertEqual(converted["function"]["parameters"]["type"], "object")

    def test_anthropic_style_messages_convert_to_openai_messages(self) -> None:
        messages = _to_openai_messages(
            "system prompt",
            [
                {"role": "user", "content": "research TESTSTOCK"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": "call-1", "name": "get_daily_plan", "input": {"instrument": "TESTSTOCK"}}
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "{}"}],
                },
            ],
        )
        self.assertEqual(messages[0], {"role": "system", "content": "system prompt"})
        self.assertEqual(messages[3]["role"], "tool")
        self.assertEqual(messages[3]["tool_call_id"], "call-1")


class ProviderFactoryTests(unittest.TestCase):
    def test_unknown_provider_rejected(self) -> None:
        with self.assertRaises(ValueError):
            create_model_client("unknown")

    def test_openai_provider_requires_key_without_constructing_network_client(self) -> None:
        with self.assertRaises(ValueError):
            OpenAICompatibleModelClient(api_key=None)

    def test_gemini_provider_requires_gemini_key(self) -> None:
        with self.assertRaises(ValueError):
            create_model_client("gemini")

    def test_ollama_provider_is_local_and_needs_no_api_key(self) -> None:
        client = create_model_client("ollama")
        self.assertIsInstance(client, OpenAICompatibleModelClient)


if __name__ == "__main__":
    unittest.main()
