"""Analyze observed multi-bagger outcomes without using them as trade inputs."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentic_investing.data import load_bars_json
from agentic_investing.features import calculate_technical_snapshot
from agentic_investing.portfolio.liquidity import rank_liquid_instruments

INDIA = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class Outcome:
    instrument: str
    endpoint_multiple: Decimal
    maximum_multiple: Decimal
    first_2x: datetime | None
    first_3x: datetime | None
    first_5x: datetime | None


@dataclass(frozen=True, slots=True)
class CrossingFeatures:
    instrument: str
    rsi: Decimal
    volume_ratio: Decimal
    close_sma50: Decimal
    trend_gap: Decimal
    close_252_high: Decimal


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze technical patterns around observed multi-baggers")
    parser.add_argument("--data-dir", default="data/real")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2021, 9, 5))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 5))
    parser.add_argument("--top-n", type=int, default=200)
    parser.add_argument("--output", default="reports/technical_portfolio/multibagger_analysis.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    start = _india_day_start(args.start)
    end = _india_day_end(args.end)
    data_dir = ROOT / args.data_dir
    ranked = rank_liquid_instruments(data_dir, top_n=args.top_n, as_of=start)
    outcomes: list[Outcome] = []
    features: list[CrossingFeatures] = []
    for rank in ranked:
        bars = [
            bar
            for bar in load_bars_json(data_dir / f"{rank.exchange.lower()}_{rank.instrument.lower()}_1d.json")
            if start <= bar.timestamp <= end
        ]
        if len(bars) < 60:
            continue
        initial_close = bars[0].close
        endpoint_multiple = bars[-1].close / initial_close
        maximum_multiple = max(bar.close for bar in bars) / initial_close
        crossings = {
            threshold: next(
                (bar.timestamp for bar in bars if bar.close / initial_close >= Decimal(threshold)),
                None,
            )
            for threshold in ("2", "3", "5")
        }
        outcomes.append(
            Outcome(
                rank.instrument,
                endpoint_multiple,
                maximum_multiple,
                crossings["2"],
                crossings["3"],
                crossings["5"],
            )
        )
        if crossings["2"] is not None:
            crossing_index = next(index for index, bar in enumerate(bars) if bar.timestamp == crossings["2"])
            visible = bars[: crossing_index + 1]
            technical = calculate_technical_snapshot(
                visible[-253:],
                min(252, len(visible) - 1),
                fast_period=20,
                slow_period=50,
                rsi_period=14,
                atr_period=14,
                volume_period=20,
            )
            if technical is not None:
                prior_closes = [bar.close for bar in bars[max(0, crossing_index - 252) : crossing_index + 1]]
                features.append(
                    CrossingFeatures(
                        rank.instrument,
                        technical.rsi,
                        technical.volume_ratio,
                        technical.close / technical.sma_slow,
                        (technical.sma_fast - technical.sma_slow) / technical.close,
                        technical.close / max(prior_closes),
                    )
                )

    report = _render_report(args, ranked, outcomes, features)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(report)
    print(f"Report written to {output}")
    return 0


def _render_report(args, ranked, outcomes: list[Outcome], features: list[CrossingFeatures]) -> str:
    lines = [
        "# Technical Multi-Bagger Analysis",
        "",
        f"Window: `{args.start.isoformat()}` to `{args.end.isoformat()}`",
        f"Universe: `{len(ranked)}` instruments ranked using volume known at the window start",
        f"Usable histories: `{len(outcomes)}`",
        "",
        "## Outcome Counts",
        "",
    ]
    for threshold in (Decimal("2"), Decimal("3"), Decimal("5")):
        lines.append(
            f"- `{threshold}x` endpoint: `{sum(item.endpoint_multiple >= threshold for item in outcomes)}`; "
            f"maximum close: `{sum(item.maximum_multiple >= threshold for item in outcomes)}`"
        )
    lines.extend(["", "## Top Endpoint Multiples", "", "| Instrument | Endpoint | Maximum |", "|---|---:|---:|"])
    for item in sorted(outcomes, key=lambda value: value.endpoint_multiple, reverse=True)[:20]:
        lines.append(f"| {item.instrument} | {item.endpoint_multiple:.2f}x | {item.maximum_multiple:.2f}x |")
    lines.extend(["", "## Top Maximum Multiples", "", "| Instrument | Endpoint | Maximum |", "|---|---:|---:|"])
    for item in sorted(outcomes, key=lambda value: value.maximum_multiple, reverse=True)[:20]:
        lines.append(f"| {item.instrument} | {item.endpoint_multiple:.2f}x | {item.maximum_multiple:.2f}x |")

    if features:
        lines.extend(
            [
                "",
                "## Technical Conditions At First 2x Crossing",
                "",
                f"Valid technical observations: `{len(features)}`",
                f"- SMA20 > SMA50: `{sum(item.trend_gap > 0 for item in features)}`",
                f"- Close > SMA50: `{sum(item.close_sma50 > 1 for item in features)}`",
                f"- Volume ratio >= 1: `{sum(item.volume_ratio >= 1 for item in features)}`",
                f"- RSI 50-70: `{sum(50 <= item.rsi <= 70 for item in features)}`",
                f"- All current entry conditions: `{sum(item.trend_gap > 0 and item.close_sma50 > 1 and item.volume_ratio >= 1 and 50 <= item.rsi <= 70 for item in features)}`",
                f"- Median RSI: `{median(float(item.rsi) for item in features):.2f}`",
                f"- Median volume ratio: `{median(float(item.volume_ratio) for item in features):.2f}`",
                f"- Median close/SMA50: `{median(float(item.close_sma50) for item in features):.3f}`",
                f"- Median close/252-day high: `{median(float(item.close_252_high) for item in features):.3f}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Caveats",
            "",
            "This is a descriptive label study, not a trading signal. The future return labels are used only for analysis, never by the backtester. The universe is based on currently retained local datasets and therefore has survivorship and selection bias. Extreme multiples require corporate-action and data-quality review before being trusted.",
            "",
        ]
    )
    return "\n".join(lines)


def _india_day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=INDIA).astimezone(UTC)


def _india_day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=INDIA).astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
