"""Daily driver: run the Claude-powered agentic reasoning loop over a watchlist.

Manual daily run, mirroring ``run_daily_shadow_update.py``'s design choices:
run once after (or before) each trading day, do not persist any in-process
state between runs beyond what ``TradeJournal`` and ``PaperBroker``/order
history already durably record.

This script does NOT place live broker orders. ``AgentRunner`` calls
``AgentToolkit.submit_trade_proposal``, which passes through
``ProposalExecutor`` -> ``RiskEngine`` -> ``OrderManager`` -> ``PaperBroker``
exactly as every other execution path in this project does. There is no
separate "live mode" flag here on purpose: switching the underlying broker
from ``PaperBroker`` to ``KiteBrokerAdapter`` is a deliberate, reviewed code
change to ``AgentToolkit``/``ProposalExecutor`` construction, not a runtime
flag that could be flipped by accident on this script's command line.

Requires ``ANTHROPIC_API_KEY`` in the environment. Requires the instrument's
local dataset to already be ingested
(see ``scripts/fetch_kite_history.py`` / ``scripts/run_daily_shadow_update.py``)
so ``get_recent_bars``/``submit_trade_proposal`` have a price basis to use.

Usage:
    .\\.venv\\Scripts\\python.exe scripts\\run_daily_agent_review.py --watchlist NIFTYBEES:NSE
    .\\.venv\\Scripts\\python.exe scripts\\run_daily_agent_review.py --watchlist NIFTYBEES:NSE RELIANCE:NSE
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.agent import AgentRunConfig, AgentRunner, AgentToolkit, RealAnthropicClient
from agentic_investing.journal import TradeJournal


def parse_watchlist_entry(entry: str) -> tuple[str, str]:
    if ":" not in entry:
        raise argparse.ArgumentTypeError(f"watchlist entry must be INSTRUMENT:EXCHANGE, got {entry!r}")
    instrument, exchange = entry.split(":", 1)
    if not instrument or not exchange:
        raise argparse.ArgumentTypeError(f"watchlist entry must be INSTRUMENT:EXCHANGE, got {entry!r}")
    return instrument.upper(), exchange.upper()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Claude-powered agentic pre-market review over a watchlist"
    )
    parser.add_argument(
        "--watchlist",
        nargs="+",
        type=parse_watchlist_entry,
        required=True,
        help="One or more INSTRUMENT:EXCHANGE pairs, e.g. NIFTYBEES:NSE RELIANCE:NSE",
    )
    parser.add_argument("--model", default=None, help="Override the default Claude model")
    parser.add_argument("--reports-dir", default="reports/agent_daily", help="Dated daily-report archive directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        client = RealAnthropicClient()
    except ValueError as error:
        print(f"Refusing to proceed: {error}", file=sys.stderr)
        return 2

    config = AgentRunConfig(model=args.model) if args.model else AgentRunConfig()
    journal = TradeJournal()
    toolkit = AgentToolkit(journal=journal)
    runner = AgentRunner(toolkit=toolkit, client=client, config=config)

    lines: list[str] = [f"# Agentic pre-market review — {datetime.now(timezone.utc).isoformat()}", ""]
    any_error = False
    for instrument, exchange in args.watchlist:
        print(f"Reviewing {instrument}:{exchange}...")
        try:
            result = runner.run_for_instrument(instrument=instrument, exchange=exchange)
        except Exception as error:  # noqa: BLE001 — one instrument failing must not abort the whole watchlist
            any_error = True
            print(f"  ERROR: {error}", file=sys.stderr)
            lines.append(f"## {instrument}:{exchange} — ERROR")
            lines.append(f"- {error}")
            lines.append("")
            continue

        lines.append(f"## {result.instrument}:{result.exchange}")
        lines.append(f"- Tools called: {', '.join(result.tool_calls) or '(none)'}")
        lines.append(f"- Proposal submitted: {result.proposal_submitted}")
        lines.append(f"- Summary: {result.final_text}")
        lines.append("")
        print(f"  tools called: {result.tool_calls}")
        print(f"  proposal submitted: {result.proposal_submitted}")

    report = "\n".join(lines)
    reports_dir = ROOT / args.reports_dir
    reports_dir.mkdir(parents=True, exist_ok=True)
    dated_path = reports_dir / f"{datetime.now(timezone.utc).date().isoformat()}.md"
    dated_path.write_text(report, encoding="utf-8")
    (reports_dir / "latest.md").write_text(report, encoding="utf-8")
    print(f"\nReport archived to {dated_path}")

    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
