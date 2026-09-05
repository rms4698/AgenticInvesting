# AgenticInvesting Engineering Guidance

This file owns contributor and coding-agent rules. See `docs/documentation-map.md` for the ownership boundary between this guidance, `README.md`, the roadmap, prompts, decisions, and generated reports.

## Mission

Build an industry-standard, world-class Python application for research-first, risk-controlled Indian-market investing through Zerodha. Risk reduction is always the first priority; profit is secondary. The 2% monthly figure is only a soft aspiration and must never force a trade.

## Non-negotiable safety rules

- Agents may research and propose. Deterministic code decides.
- `RiskEngine` and `OrderManager` are the only sanctioned path to a broker call.
- There must be no direct `place_order` path from an LLM, MCP tool, prompt, or script.
- New entries are risk-gated; exits must remain possible even during risk-limit or kill-switch conditions.
- Default to `PaperBroker`. Live execution requires an explicit, reviewed change and staged validation.
- Treat stale, missing, contradictory, or unverifiable market information as a reason to prefer `HOLD`.
- Never use unverified data as the sole basis for a trade.

## Prompt and instruction management

- Store all long-form prompts and instructions as reviewed Markdown files under `docs/prompts/`.
- Load prompts through `agentic_investing.config.load_prompt`; do not hardcode prompts in Python files.
- Prompt changes are behavior changes and require tests or an explicit review note.

## Python standards

- Use Python 3.13, type annotations, immutable value objects where appropriate, UTC-aware datetimes, and `Decimal` for money.
- Avoid local imports. Imports belong at module scope so missing dependencies and circular-import problems fail visibly at startup. Exceptions require a documented platform boundary or optional dependency strategy.
- Prefer small, composable modules with explicit protocols and dependency injection for external services.
- Use deterministic unit tests for every tool and risk boundary. External network calls must be mocked in unit tests.
- Do not silently swallow exceptions. Log key context and re-raise or return an explicit failure result.
- Use `logging`, never ad-hoc debug prints. Logs should capture lifecycle events, external calls, tool names, risk outcomes, order outcomes, and failures without credentials, tokens, full prompts, or sensitive payloads.
- Use `get_logger(__name__)` from `agentic_investing.logging_config` for application logging. The configured file is `logs/agentic_investing.log`; `logs/` is ignored by Git.
- Keep tool schemas derived from type-annotated method signatures. Do not duplicate schemas by hand.
- Validate changes with the project venv explicitly: `c:/Dropbox/muthusar/AgenticInvesting/.venv/Scripts/python.exe -m pytest tests/ -q`.
- Use workspace-wide Pylance diagnostics. `.vscode/settings.json` must retain `python.analysis.diagnosticMode: "workspace"`.

## Data-source policy

- Use Zerodha Kite Connect as the broker-authoritative market-data source for OHLCV and execution state.
- Use the configured provider's supported web-search/research capability for current Indian news, filings, corporate announcements, and ad-hoc fundamentals. Claude currently has native web search; providers without it must prefer `HOLD` when current information is required rather than inventing facts. Prefer primary or authoritative sources and require source/date-aware reasoning.
- Do not add unofficial NSE/Screener/Trendlyne scraping as a hard dependency.

## Git policy

- **Never commit automatically.** Only stage or commit when the user explicitly asks.
- Keep temporary files, local logs, secrets, caches, and generated reports out of Git.
- Do not commit `.tmp_mcp_test/`; it is ignored in `.gitignore`.
- Never commit API keys, access tokens, broker sessions, or local SQLite journal data.
