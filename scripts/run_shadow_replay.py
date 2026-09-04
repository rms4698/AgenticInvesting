"""Replay a fetched historical dataset through ShadowTradingSession.

This simulates shadow trading by feeding recorded bars one at a time. It is
useful for a quick sanity check and for producing a sample daily report, but
it is not a substitute for running against a genuinely live intraday feed.
No real orders are placed.
"""

import argparse
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.data import load_bars_json
from agentic_investing.shadow import ShadowSessionConfig, ShadowTradingSession
from agentic_investing.strategies import SmaCrossoverStrategy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay bars through a shadow trading session")
    parser.add_argument("--dataset", required=True, help="Path to a JSON dataset from ingest_historical_bars")
    parser.add_argument("--fast-period", type=int, default=20)
    parser.add_argument("--slow-period", type=int, default=50)
    parser.add_argument("--output", default=None, help="Optional path to write the daily report")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bars = load_bars_json(args.dataset)
    print(f"Loaded {len(bars)} bars from {args.dataset}")

    strategy = SmaCrossoverStrategy(fast_period=args.fast_period, slow_period=args.slow_period)
    config = ShadowSessionConfig(
        initial_capital=Decimal("100000"),
        commission_rate=Decimal("0.0003"),
        slippage_rate=Decimal("0.0005"),
        stop_distance_fraction=Decimal("0.05"),
    )
    session = ShadowTradingSession(strategy=strategy, config=config)

    for bar in bars:
        session.on_bar(bar)

    report = session.daily_report()
    print()
    print(report)

    if args.output:
        output_path = ROOT / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
        print(f"Report written to {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
