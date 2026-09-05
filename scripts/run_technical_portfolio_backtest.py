"""Run the technical-only five-year multi-instrument portfolio backtest."""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("AGENTIC_INVESTING_LOG_LEVEL", "ERROR")

from agentic_investing.data import load_bars_json
from agentic_investing.logging_config import get_logger
from agentic_investing.portfolio import (
    TechnicalOnlyBacktester,
    TechnicalOnlyConfig,
    rank_liquid_instruments,
)
from agentic_investing.risk import RiskLimits

LOGGER = get_logger(__name__)
INDIA = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the technical-only liquid-equity portfolio")
    parser.add_argument("--data-dir", default="data/real")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 9, 5))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 5))
    parser.add_argument("--top-n", type=int, default=200)
    parser.add_argument("--universe-mode", choices=("fixed", "dynamic"), default="fixed")
    parser.add_argument("--initial-capital", type=Decimal, default=Decimal("100000"))
    parser.add_argument("--max-positions", type=int, default=8)
    parser.add_argument("--maximum-rsi", type=Decimal, default=Decimal("100"))
    parser.add_argument("--exit-mode", choices=("target", "trailing"), default="trailing")
    parser.add_argument("--trailing-stop-atr", type=Decimal, default=Decimal("3"))
    parser.add_argument("--enable-pyramiding", action="store_true")
    parser.add_argument("--max-pyramid-additions", type=int, default=2)
    parser.add_argument("--pyramid-trigger-atr", type=Decimal, default=Decimal("1"))
    parser.add_argument(
        "--regime-dataset",
        default="data/real/nse_niftybees_1d.json",
        help="Daily benchmark dataset for the 50/200 regime gate; pass an empty value to disable",
    )
    parser.add_argument("--risk-per-trade-fraction", type=Decimal, default=Decimal("0.0025"))
    parser.add_argument("--max-open-risk-fraction", type=Decimal, default=Decimal("0.01"))
    parser.add_argument("--require-weekly-confirmation", action="store_true")
    parser.add_argument("--require-relative-strength", action="store_true")
    parser.add_argument("--require-52-week-proximity", action="store_true")
    parser.add_argument("--entry-mode", choices=("trend", "breakout"), default="trend")
    parser.add_argument("--output", default="reports/technical_portfolio/2021-09-05_to_2026-09-05.md")
    parser.add_argument("--allocation-output", default="reports/technical_portfolio/allocation_history.csv")
    parser.add_argument("--trade-ledger-output", default="reports/technical_portfolio/trade_ledger.csv")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.start > args.end:
        raise ValueError("start must not be after end")
    start = _india_day_start(args.start)
    end = _india_day_end(args.end)
    data_dir = ROOT / args.data_dir
    ranked = rank_liquid_instruments(
        data_dir,
        top_n=args.top_n,
        as_of=start.replace(hour=23, minute=59, second=59),
    )
    if args.universe_mode == "fixed":
        dataset_paths = [
            data_dir / f"{rank.exchange.lower()}_{rank.instrument.lower()}_1d.json"
            for rank in ranked
        ]
    else:
        dataset_paths = sorted(data_dir.glob("nse_*_1d.json"))
    bars_by_instrument = {}
    missing = []
    for path in dataset_paths:
        try:
            bars = load_bars_json(path)
            if bars:
                bars_by_instrument[bars[0].instrument] = bars
        except (OSError, ValueError) as error:
            missing.append(f"{path.stem} ({error})")

    regime_bars = load_bars_json(ROOT / args.regime_dataset) if args.regime_dataset else None

    config = TechnicalOnlyConfig(
        max_positions=args.max_positions,
        maximum_rsi=args.maximum_rsi,
        use_profit_target=args.exit_mode == "target",
        trailing_stop_atr_multiple=args.trailing_stop_atr if args.exit_mode == "trailing" else None,
        require_weekly_confirmation=args.require_weekly_confirmation,
        require_relative_strength=args.require_relative_strength,
        require_52_week_proximity=args.require_52_week_proximity,
        require_breakout=args.entry_mode == "breakout",
        enable_pyramiding=args.enable_pyramiding,
        max_pyramid_additions=args.max_pyramid_additions,
        pyramid_trigger_atr_multiple=args.pyramid_trigger_atr,
        start=start,
        end=end,
    )
    result = TechnicalOnlyBacktester(
        config=config,
        initial_capital=args.initial_capital,
        market_regime_bars=regime_bars,
        risk_limits=RiskLimits(
            account_capital=args.initial_capital,
            risk_per_trade_fraction=args.risk_per_trade_fraction,
            max_open_portfolio_risk_fraction=args.max_open_risk_fraction,
            max_positions=args.max_positions,
        ),
    ).run(bars_by_instrument)
    benchmark_return = _benchmark_return(regime_bars, start, end)
    report = _render_report(args, ranked, missing, result, benchmark_return)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    _write_allocation_history(ROOT / args.allocation_output, result)
    _write_trade_ledger(ROOT / args.trade_ledger_output, result)
    LOGGER.info(
        "technical_portfolio_backtest_complete instruments=%d final_capital=%s total_return=%s",
        result.candidate_count,
        result.final_capital,
        result.metrics.total_return,
    )
    print(report)
    print(f"Report written to {output}")
    print(f"Allocation history written to {ROOT / args.allocation_output}")
    print(f"Trade ledger written to {ROOT / args.trade_ledger_output}")
    return 0


def _render_report(args: argparse.Namespace, ranked, missing, result, benchmark_return: Decimal | None) -> str:
    metrics = result.metrics
    trade_count = metrics.trade_count
    win_rate = Decimal(metrics.winning_trades) / Decimal(trade_count) if trade_count else Decimal("0")
    max_deployment = max((item.deployment_fraction for item in result.allocation_history), default=Decimal("0"))
    final_allocation = result.allocation_history[-1] if result.allocation_history else None
    lines = [
        "# Technical-Only Portfolio Backtest",
        "",
        "## Configuration",
        "",
        f"- Window: `{args.start.isoformat()}` to `{args.end.isoformat()}` ({(result.end - result.start).days / 365.25:.2f} years)",
        f"- Initial capital: `₹{result.initial_capital:,.2f}`",
        f"- Initial liquidity snapshot: `{len(ranked)}` instruments",
        f"- Loaded/backtested histories: `{result.candidate_count}` local NSE cash-equity datasets",
        f"- Maximum open positions: `{args.max_positions}`",
        f"- Universe mode: `{args.universe_mode}`",
        f"- Active universe: top `{args.top_n}` by 20-day traded value, refreshed every 21 trading days when dynamic",
        "- Data frequency: daily bars; signals use the closed bar and execute at the next available bar open",
        f"- Market regime gate: `{'50/200 benchmark trend' if args.regime_dataset else 'disabled'}`",
        f"- Risk budget: `{args.risk_per_trade_fraction * 100:.2f}%` per trade; `{args.max_open_risk_fraction * 100:.2f}%` open portfolio risk",
        f"- Entry mode: `{args.entry_mode}`; daily SMA(20) > SMA(50), RSI 50-{args.maximum_rsi}, volume ratio >= 1.0",
        f"- Pyramiding: `{'enabled' if args.enable_pyramiding else 'disabled'}`; max additions=`{args.max_pyramid_additions}`, trigger=`{args.pyramid_trigger_atr} ATR`",
        "- Weekly trend, relative strength, 52-week proximity, volatility contraction, and breakout quality rank candidates",
        f"- Hard gates: weekly=`{args.require_weekly_confirmation}`, relative-strength=`{args.require_relative_strength}`, 52-week=`{args.require_52_week_proximity}`",
        f"- Exit: 2 ATR stop, {f'{args.trailing_stop_atr} ATR trailing stop' if args.exit_mode == 'trailing' else '3 ATR target'}, or SMA(20) < SMA(50)",
        "",
        "## Results",
        "",
        f"- Final capital: `₹{result.final_capital:,.2f}`",
        f"- Total return: `{metrics.total_return * 100:.2f}%`",
        f"- CAGR: `{result.cagr * 100:.2f}%`",
        f"- Maximum drawdown: `{metrics.max_drawdown * 100:.2f}%`",
        f"- Annualized volatility: `{metrics.annualized_volatility * 100:.2f}%`",
        f"- Sharpe ratio: `{metrics.sharpe_ratio:.2f}`",
        f"- Trades: `{trade_count}`; winning: `{metrics.winning_trades}`; losing: `{metrics.losing_trades}`; win rate: `{win_rate * 100:.2f}%`",
        f"- Profit factor: `{metrics.profit_factor:.2f}`",
        f"- NIFTYBEES buy-and-hold return: `{benchmark_return * 100:.2f}%`" if benchmark_return is not None else "- NIFTYBEES buy-and-hold return: `unavailable`",
        f"- Strategy excess return versus NIFTYBEES: `{(metrics.total_return - benchmark_return) * 100:.2f}%`" if benchmark_return is not None else "- Strategy excess return versus NIFTYBEES: `unavailable`",
        "",
        "## Allocation",
        "",
        f"- Maximum capital deployed: `{max_deployment * 100:.2f}%`",
        f"- Average capital deployed: `{result.average_deployment_fraction * 100:.2f}%`",
        f"- Maximum positions held: `{result.max_positions_held}`",
        f"- Hard-drawdown kill switch: `{'TRIGGERED' if result.kill_switch_triggered else 'not triggered'}`",
    ]
    if result.kill_switch_reason:
        lines.append(f"- Kill-switch reason: `{result.kill_switch_reason}`")
    if final_allocation is not None:
        lines.append(
            f"- Final allocation: `{final_allocation.position_count}` positions, "
            f"`₹{final_allocation.deployed_capital:,.2f}` deployed"
        )
    if missing:
        lines.extend(["", "## Skipped datasets", "", *[f"- {item}" for item in missing]])
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "This is a research backtest, not a return guarantee or live recommendation. "
            "The universe is selected from currently available local Kite datasets, so "
            "survivorship and current-universe selection bias remain. The backtest does "
            "not use fundamentals or future bars for the technical decisions.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_allocation_history(path: Path, result) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("timestamp", "position_count", "deployed_capital", "deployment_fraction", "instruments"))
        for snapshot in result.allocation_history:
            writer.writerow(
                (
                    snapshot.timestamp.isoformat(),
                    snapshot.position_count,
                    str(snapshot.deployed_capital),
                    str(snapshot.deployment_fraction),
                    ";".join(snapshot.instruments),
                )
            )


def _write_trade_ledger(path: Path, result) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            (
                "instrument",
                "exchange",
                "entry_time",
                "exit_time",
                "entry_price",
                "exit_price",
                "quantity",
                "pnl",
                "exit_reason",
            )
        )
        for trade in result.trade_records:
            writer.writerow(
                (
                    trade.instrument,
                    trade.exchange,
                    trade.entry_time.isoformat(),
                    trade.exit_time.isoformat(),
                    str(trade.entry_price),
                    str(trade.exit_price),
                    trade.quantity,
                    str(trade.pnl),
                    trade.exit_reason,
                )
            )


def _benchmark_return(bars, start: datetime, end: datetime) -> Decimal | None:
    if not bars:
        return None
    visible = [bar for bar in bars if start <= bar.timestamp <= end]
    if len(visible) < 2:
        return None
    return visible[-1].close / visible[0].close - Decimal("1")


def _india_day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=INDIA).astimezone(UTC)


def _india_day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=INDIA).astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
