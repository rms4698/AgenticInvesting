import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agentic_investing.agent.tools import TOOL_METHOD_NAMES, AgentToolkit, build_anthropic_tool_schemas
from agentic_investing.config import load_prompt, prompt_path
from agentic_investing.logging_config import configure_logging, get_logger, shutdown_logging


class PromptCatalogTests(unittest.TestCase):
    def test_system_and_task_prompts_load_from_markdown(self) -> None:
        system = load_prompt("agent_system.md")
        task = load_prompt("agent_task.md")
        self.assertIn("Reduce risk", system)
        self.assertIn("{instrument}", task)

    def test_prompt_path_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            prompt_path("../secrets.md")
        with self.assertRaises(ValueError):
            prompt_path("not-a-markdown.txt")


class DynamicToolSchemaTests(unittest.TestCase):
    def test_tool_registry_is_the_single_dynamic_schema_source(self) -> None:
        toolkit = AgentToolkit()
        schemas = build_anthropic_tool_schemas(toolkit)
        self.assertEqual(tuple(schema["name"] for schema in schemas), TOOL_METHOD_NAMES)
        self.assertIn("submit_trade_proposal", TOOL_METHOD_NAMES)
        self.assertTrue(all(schema["input_schema"]["type"] == "object" for schema in schemas))


class LoggingConfigurationTests(unittest.TestCase):
    def test_logging_configures_console_and_rotating_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            configure_logging(log_dir=temp_dir, force=True)
            logger = get_logger("test")
            logger.info("test_event")
            log_path = Path(temp_dir) / "agentic_investing.log"
            self.assertTrue(log_path.exists())
            self.assertIn("test_event", log_path.read_text(encoding="utf-8"))
            shutdown_logging()


if __name__ == "__main__":
    unittest.main()
