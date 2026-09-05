# Decision 0002 — Algorithm-first, then portfolio selection

**Date:** 2026-09-05  
**Status:** Accepted

## Decision

Use a two-stage deterministic roadmap:

1. **Strategy validation first:** compare several explainable algorithms with rolling walk-forward tests, realistic costs, drawdown gates, and paper/shadow execution.
2. **Portfolio selection second:** build a deterministic stock-universe pipeline that combines approved technical and fundamental features, ranks candidates, applies portfolio/risk constraints, and produces BUY/SELL/HOLD proposals with an attached stop-loss and target.

No strategy is activated automatically by a backtest result. A human reviews the comparison report and explicitly selects the candidate for paper mode.

## Why

Strategy validation isolates the trading engine, sizing, execution timing, stop/target behavior, and risk controls. Building stock screening and portfolio construction first would mix data-quality, universe, ranking, allocation, and execution failures together, making results difficult to explain.

The second stage is still necessary for the real portfolio objective: selecting among a broad Indian equity universe rather than repeatedly testing one instrument. It must be deterministic and source-aware. Web/MCP research may provide evidence, but it cannot override the ranking or risk gates.

## Current implementation

- SMA crossover and Donchian breakout are implemented.
- `compare_strategies()` ranks candidates using out-of-sample return, drawdown, Sharpe, positive-window count, and return-to-drawdown score.
- `scripts/run_strategy_selection.py` produces the comparison report.
- The latest NIFTYBEES comparison selected Donchian 20 as the research candidate, but it was not automatically activated.

## Next decision gates

- Select one deterministic candidate for paper/shadow observation.
- Define the initial stock universe and liquidity rules.
- Add versioned screening features and portfolio allocation constraints.
- Backtest the full screen-to-portfolio workflow with survivorship/look-ahead controls.
- Attach deterministic stop-loss/target levels before any paper order.
