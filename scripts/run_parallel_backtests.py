"""Run a bounded fixed-universe backtest matrix in parallel worker processes."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.data import load_bars_json
from agentic_investing.portfolio.liquidity import rank_liquid_instruments
from agentic_investing.portfolio.technical_only import TechnicalOnlyBacktester, TechnicalOnlyConfig
from agentic_investing.risk import RiskLimits

INDIA = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fixed-universe technical backtests in parallel")
    parser.add_argument("--data-dir", default="data/real")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 9, 5))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 5))
    parser.add_argument("--top-n", type=int, default=200)
    parser.add_argument("--trailing-stops", default="2,3,5")
    parser.add_argument("--max-positions", default="8,12")
    parser.add_argument("--workers", type=int, default=min(os.cpu_count() or 1, 4))
    parser.add_argument("--output", default="reports/technical_portfolio/parallel_matrix.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = _india_day_start(args.start)
    end = _india_day_end(args.end)
    data_dir = ROOT / args.data_dir
    ranked = rank_liquid_instruments(data_dir, top_n=args.top_n, as_of=start)
    symbols = tuple(rank.instrument for rank in ranked)
    trailing_stops = tuple(Decimal(value.strip()) for value in args.trailing_stops.split(",") if value.strip())
    max_positions = tuple(int(value.strip()) for value in args.max_positions.split(",") if value.strip())
    cases = tuple(
        {
            "data_dir": str(data_dir),
            "symbols": symbols,
            "benchmark_path": str(data_dir / "nse_niftybees_1d.json"),
            "start": start,
            "end": end,
            "trailing_stop_atr": trailing_stop,
            "max_positions": positions,
        }
        for trailing_stop in trailing_stops
        for positions in max_positions
    )
    with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
        results = list(executor.map(_run_case, cases))
    results.sort(key=lambda row: (row["trailing_stop_atr"], row["max_positions"]))
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=results[0].keys())
        writer.writeheader()
        writer.writerows(results)
    print(f"Completed {len(results)} cases with {max(1, args.workers)} workers")
    for result in results:
        print(
            f"trail={result['trailing_stop_atr']} positions={result['max_positions']} "
            f"return={result['total_return']} cagr={result['cagr']} "
            f"drawdown={result['max_drawdown']} sharpe={result['sharpe']}"
        )
    print(f"Results written to {output}")
    return 0


def _run_case(case: dict) -> dict[str, str | int]:
    bars_by_instrument = {
        symbol: load_bars_json(Path(case["data_dir"]) / f"nse_{symbol.lower()}_1d.json")
        for symbol in case["symbols"]
    }
    benchmark = load_bars_json(Path(case["benchmark_path"]))
    config = TechnicalOnlyConfig(
        max_positions=case["max_positions"],
        maximum_rsi=Decimal("100"),
        minimum_volume_ratio=Decimal("1"),
        use_profit_target=False,
        trailing_stop_atr_multiple=case["trailing_stop_atr"],
        start=case["start"],
        end=case["end"],
    )
    limits = RiskLimits(
        account_capital=Decimal("100000"),
        risk_per_trade_fraction=Decimal("0.0025"),
        max_open_portfolio_risk_fraction=Decimal("0.01"),
        max_positions=case["max_positions"],
    )
    result = TechnicalOnlyBacktester(
        config=config,
        market_regime_bars=benchmark,
        risk_limits=limits,
    ).run(bars_by_instrument)
    metrics = result.metrics
    return {
        "trailing_stop_atr": str(case["trailing_stop_atr"]),
        "max_positions": case["max_positions"],
        "total_return": str(metrics.total_return),
        "cagr": str(result.cagr),
        "max_drawdown": str(metrics.max_drawdown),
        "sharpe": str(metrics.sharpe_ratio),
        "trade_count": metrics.trade_count,
        "average_deployment": str(result.average_deployment_fraction),
        "kill_switch": result.kill_switch_triggered,
    }


def _india_day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=INDIA).astimezone(UTC)


def _india_day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=INDIA).astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
