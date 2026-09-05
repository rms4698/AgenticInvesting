"""Daily decision driver with explicit AI/algo switching.

The selected mode changes only the decision producer. Both modes remain
paper/shadow-only and share the same local data, risk limits, journal, and
execution boundary. AI mode supports Claude, OpenAI, and DeepSeek-compatible
providers; algorithm mode needs no AI API key.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.agent import AgentRunConfig, AgentRunner, AgentToolkit, create_model_client
from agentic_investing.data.json_loader import load_bars_json
from agentic_investing.journal import TradeJournal
from agentic_investing.logging_config import get_logger
from agentic_investing.shadow import ShadowSessionConfig, ShadowTradingSession
from agentic_investing.strategies import SmaCrossoverStrategy

LOGGER = get_logger(__name__)


def parse_watchlist_entry(entry: str) -> tuple[str, str]:
    if ":" not in entry:
        raise argparse.ArgumentTypeError(f"watchlist entry must be INSTRUMENT:EXCHANGE, got {entry!r}")
    instrument, exchange = entry.split(":", 1)
    if not instrument or not exchange:
        raise argparse.ArgumentTypeError(f"watchlist entry must be INSTRUMENT:EXCHANGE, got {entry!r}")
    return instrument.upper(), exchange.upper()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the daily AI or deterministic algorithm review")
    parser.add_argument("--mode", choices=("ai", "algo"), default="ai")
    parser.add_argument("--provider", choices=("claude", "openai", "deepseek", "gemini", "ollama"), default="claude")
    parser.add_argument("--watchlist", nargs="+", type=parse_watchlist_entry, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--reports-dir", default="reports/agent_daily")
    return parser.parse_args()


def _dataset_path(instrument: str, exchange: str) -> Path:
    return ROOT / "data" / "real" / f"{exchange.lower()}_{instrument.lower()}_1d.json"


def _run_algo(instrument: str, exchange: str) -> str:
    bars = load_bars_json(_dataset_path(instrument, exchange))
    session = ShadowTradingSession(
        strategy=SmaCrossoverStrategy(fast_period=20, slow_period=50),
        config=ShadowSessionConfig(initial_capital=Decimal("100000")),
    )
    for bar in bars:
        session.on_bar(bar)
    return session.daily_report()


def _run_ai(args: argparse.Namespace, watchlist: list[tuple[str, str]]) -> tuple[str, bool]:
    if args.provider != "claude":
        LOGGER.warning("provider_web_search_disabled provider=%s reason=native_web_search_is_claude_only", args.provider)
    client = create_model_client(args.provider)
    default_model = {
        "claude": "claude-sonnet-4-5",
        "openai": "gpt-4o-mini",
        "deepseek": "deepseek-chat",
        "gemini": "gemini-2.5-flash",
        "ollama": "qwen2.5:7b",
    }[args.provider]
    config = AgentRunConfig(model=args.model or default_model, enable_web_search=args.provider == "claude")
    toolkit = AgentToolkit(journal=TradeJournal())
    try:
        runner = AgentRunner(toolkit=toolkit, client=client, config=config)
        lines = [f"# AI review ({args.provider}) — {datetime.now(timezone.utc).isoformat()}", ""]
        any_error = False
        for instrument, exchange in watchlist:
            LOGGER.info("review_started instrument=%s exchange=%s provider=%s", instrument, exchange, args.provider)
            try:
                result = runner.run_for_instrument(instrument=instrument, exchange=exchange)
                lines.extend(
                    [
                        f"## {result.instrument}:{result.exchange}",
                        f"- Client tools called: {', '.join(result.tool_calls) or '(none)'}",
                        f"- Web searches: {result.web_search_count}",
                        f"- Proposal submitted: {result.proposal_submitted}",
                        f"- Summary: {result.final_text}",
                        "",
                    ]
                )
            except Exception as error:  # noqa: BLE001
                any_error = True
                LOGGER.exception("review_failed instrument=%s exchange=%s", instrument, exchange)
                lines.extend([f"## {instrument}:{exchange} — ERROR", f"- {error}", ""])
        return "\n".join(lines), any_error
    finally:
        toolkit.close()


def main() -> int:
    args = parse_args()
    any_error = False
    if args.mode == "algo":
        lines = [f"# Algorithm review — {datetime.now(timezone.utc).isoformat()}", ""]
        for instrument, exchange in args.watchlist:
            try:
                lines.extend([f"## {instrument}:{exchange}", _run_algo(instrument, exchange), ""])
            except Exception as error:  # noqa: BLE001
                any_error = True
                LOGGER.exception("algorithm_review_failed instrument=%s exchange=%s", instrument, exchange)
                lines.extend([f"## {instrument}:{exchange} — ERROR", f"- {error}", ""])
        report = "\n".join(lines)
    else:
        try:
            report, any_error = _run_ai(args, args.watchlist)
        except ValueError as error:
            print(f"Refusing to proceed: {error}", file=sys.stderr)
            return 2

    reports_dir = ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    dated_path = reports_dir / f"{datetime.now(timezone.utc).date().isoformat()}.md"
    dated_path.write_text(report, encoding="utf-8")
    (reports_dir / "latest.md").write_text(report, encoding="utf-8")
    print(f"Report archived to {dated_path}")
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
