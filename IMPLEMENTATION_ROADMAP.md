# Agentic Investing Platform — India

Document ownership is defined in [`docs/documentation-map.md`](docs/documentation-map.md). This file intentionally stays short: it owns the overall goal, completed work, next steps, and release gates. Detailed engineering rules live in `AGENTS.md`; current usage lives in `README.md`.

## Overall goal

Build a world-class Python platform for research-first, risk-controlled Indian-market investing through Zerodha.

The platform must:

- Prioritize capital preservation over return chasing.
- Use reliable, timestamped, reproducible market data.
- Support deterministic algorithms and provider-neutral AI research.
- Keep AI outputs advisory only.
- Route every proposed entry through `RiskEngine` and `OrderManager`.
- Keep exits possible during stale-data, loss-limit, and kill-switch conditions.
- Begin with paper/shadow operation and only later consider very small live capital.

The 2% monthly figure is a soft research aspiration, never a quota or reason to force a trade.

## Done

- Versioned Zerodha historical-data ingestion with validation, look-ahead protection, token verification, and backups.
- SMA baseline backtesting and shadow trading with realistic costs, sizing, stop-losses, targets, drawdown controls, and kill switch.
- Paper broker, idempotent order manager, reconciliation, and a tested Zerodha adapter that is not automated live.
- Durable trade journal and structured `TradeProposal` boundary.
- MCP server with dynamically derived schemas and no direct `place_order` tool.
- Provider-neutral AI adapters for Claude, OpenAI, DeepSeek, Gemini-compatible APIs, and local Ollama.
- Claude native web search for current Indian research; non-Claude providers fail closed when current research is unavailable.
- Explicit `--mode algo|ai` operation switch.
- SMA and Donchian deterministic strategies plus walk-forward, risk-aware comparison. Current NIFTYBEES research selected Donchian 20 as the better candidate in the latest comparison, but it has not been activated automatically. See `docs/decisions/0002-algorithm-first-then-portfolio.md`.
- Initial Stage 2 portfolio layer: local SMA/RSI/ATR/volume features, timestamp-aware fundamentals snapshots, deterministic screening/ranking, stop/target plans, a multi-instrument paper portfolio backtester, an approved universe configuration, and a batch Kite ingestion command. See `docs/portfolio-stage2.md`.
- Workspace-wide diagnostics, structured logging, external Markdown prompts, unit tests, and contributor guidance.

## Next steps

1. Select one deterministic candidate manually from the current walk-forward comparison for paper mode.
2. Run that algorithm in daily shadow mode for a predefined observation period covering multiple market conditions.
3. Run `scripts/fetch_portfolio_universe.py` with a fresh Kite session, then add approved fundamentals snapshots; the current NIFTYBEES dataset is single-instrument and cannot prove cross-sectional portfolio selection.
4. Run the Stage 2 portfolio backtester across a broad, survivorship-aware universe and review allocation/concentration behavior.
5. Add read-only Kite portfolio, margin, quote, order-history, and trade-history tools behind our own risk-aware toolkit boundary.
6. Use AI providers only as research/advisory overlays and compare their proposals against the selected algorithm.
7. Complete reconciliation, operational monitoring, and manual emergency procedures before any live pilot.
8. Consider very small live capital only after all release gates pass and the change is explicitly reviewed.

## Release gates

Do not move to live execution until:

- Backtests and walk-forward results are reproducible after costs and slippage.
- No candidate strategy exceeds the approved drawdown/loss limits.
- Paper/shadow operation has no unexplained order-state mismatches.
- Risk controls, stops, reconciliation, and kill switch are tested during failures and restarts.
- Every live order remains explainable and traceable to data, strategy, configuration, and approval records.
- The operator can stop new orders and flatten positions manually.

This document is software planning, not investment, tax, legal, or regulatory advice.
