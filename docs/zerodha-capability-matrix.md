# Zerodha Capability Matrix

## What Zerodha provides

Official Kite Connect covers authentication, instruments, quotes/LTP/OHLC, historical candles, WebSocket streaming, holdings, positions, margins, orders, trades, GTTs, mutual funds, and related portfolio/order workflows. Zerodha's official `kite-mcp-server` exposes most of that surface, including direct order placement.

## What this application uses today

| Capability | Current status | Reason |
|---|---|---|
| Instrument master/token lookup | Used | Required to prevent wrong-instrument data incidents. |
| Historical OHLCV | Used | Authoritative source for backtests and shadow data. |
| PaperBroker/order models | Used | Safe execution path for paper/shadow testing. |
| Kite live broker adapter | Implemented but not automated | Requires explicit staged live decision. |
| RiskEngine + OrderManager | Used | Mandatory gate before any broker call. |
| Holdings/positions/margins | Adapter support exists, but not exposed to the AI runner yet | Next integration slice for reconciliation and portfolio context. |
| Live quotes/LTP/OHLC | Not yet exposed to the AI runner | Needed before any live/premarket workflow relies on current prices. |
| WebSocket streaming | Not yet wired into the agent workflow | Useful for monitoring and exits, not required for daily EOD shadow mode. |
| Orders/trades/order history | Adapter support exists, but not exposed as AI tools | Needed for live reconciliation and operator reporting. |
| GTT | Future live-pilot backstop | Must remain subordinate to platform risk logic. |
| Mutual funds | Out of current stock-only scope | Future scope. |
| Zerodha hosted MCP | Deliberately not used directly | It exposes direct trading operations without this project's deterministic risk gate. We may reuse read-only ideas or wrap selected read-only calls behind our own gate later. |

## Screener question

Zerodha/Kite Connect does **not** provide a general stock screener or fundamentals-ranking API comparable to a Screener/Trendlyne-style product. `search_instruments` is instrument lookup, not investment screening. The hosted Kite MCP provides market/account/order tools, not a general fundamentals screener.

The current research approach is therefore:

1. Kite-authoritative OHLCV and execution state.
2. Deterministic local technical analysis and backtesting.
3. Claude native web search for current Indian filings, announcements, and financial research.
4. HOLD when current information is missing, contradictory, or unverifiable.

The application now supports `selection_mode: "all_equity"` in
`config/portfolio_universe.json`. This means the account is not restricted to
the seven original examples: the batch job discovers current NSE cash-equity
symbols from Kite's instrument master. Discovery is still bounded per run so
historical downloads, API usage, and data-quality review remain operationally
manageable. Liquidity, fundamentals, and portfolio-risk filters decide what
can become a candidate later; account permissions and universe discovery are
separate concerns.

We should add read-only Kite portfolio/quote tools only through our own MCP/toolkit boundary, and never expose a raw direct-order tool to an AI provider.
