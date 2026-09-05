# Fundamentals Source Policy

## Decision

Do not use Zerodha/Kite Connect as a fundamentals provider. Kite provides instruments, quotes, candles, portfolio, orders, margins, and related broker workflows; it does not provide a normalized company fundamentals endpoint.

Do not make Screener.in, Trendlyne, Moneycontrol, or NSE/BSE website-internal endpoints a hard scraping dependency. They may be useful for human/web research, but automated scraping is fragile and subject to site terms and access changes.

## Source hierarchy

1. **Primary authoritative filings**
   - NSE corporate filings, financial results, annual reports, XBRL results, and announcements.
   - BSE financial results, XBRL results, annual reports, and corporate announcements.
   - Company investor-relations filings and exchange-distributed reports.
   - MCA AOC-4/AOC-4 XBRL and related statutory filings where accessible and appropriate.
2. **Licensed normalized datasets**
   - CMIE Prowess, Ace Equity, Capitaline, Bloomberg, LSEG/Refinitiv, FactSet, S&P Capital IQ, or another provider with an explicit redistribution/API license.
   - These are better for large-scale normalized ratios and historical fundamentals, but pricing and licensing must be verified before integration.
3. **Web research**
   - Claude native web search or another approved search provider for current announcements, filings, management commentary, and source discovery.
   - Search output is evidence, not a database. Store source URL, publication date, retrieval time, and extracted values before using it in a backtest or proposal.

## Current implementation boundary

`FundamentalSnapshot` is the internal contract. A snapshot must contain:

- instrument and exchange
- source identifier
- timezone-aware `available_at`
- decimal-string values for the metrics used by a screen
- sector classification

Backtests reject a snapshot that was not available by the decision timestamp. Missing or contradictory fundamentals produce `HOLD`/candidate rejection.

The next production-quality fundamentals integration should be one of:

- an approved licensed normalized provider adapter, or
- a controlled filing-ingestion pipeline for a smaller liquid universe that parses NSE/BSE/company filings into versioned snapshots.

A free unofficial scraper is not an acceptable substitute for either.
