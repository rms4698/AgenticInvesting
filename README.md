# Agentic Investing — India

Research-first, risk-controlled framework for Indian-market strategy research and staged Zerodha integration.

## Current milestone

This repository currently contains the Phase 0/Phase 1 foundation, Phase 2 data foundation, and Phase 3 backtesting/validation foundation:

- Agreed scope and risk charter
- Explicit configurable risk defaults for a ₹1,00,000 account
- Minimal Python package structure
- Unit tests for risk-limit validation
- Canonical OHLCV bar model for Indian-market research
- CSV ingestion with SHA-256 dataset fingerprinting
- Data-quality checks for duplicates, chronology, OHLC relationships, volume, and look-ahead risk
- Versioned sample NSE data fixture for offline tests
- Long-only SMA crossover baseline strategy
- Next-bar-open execution model with configurable commission and slippage
- Risk-based position sizing using the agreed ₹500 per-trade budget
- Portfolio accounting, forced end-of-series liquidation, and performance metrics
- Chronological train/test evaluation with indicator warmup isolation
- Rolling walk-forward window generation and evaluation
- Buy-and-hold and cash benchmarks
- Commission/slippage sensitivity scenarios
- Deterministic Markdown validation report rendering
- Optional Yahoo Finance provider for free exploratory daily-bar research
- First real-data validation run: NIFTYBEES 2018-2025 via Kite, SMA(20,50) baseline (see `reports/niftybees_sma_20_50.md`)
- Automatic Kite historical-data date-range chunking (per-interval max-span limits)
- Kite instrument-token lookup script (`scripts/lookup_kite_instrument.py`)
- Session-aware `fetch_kite_history.py` (no manual access-token env var needed)
- End-to-end research evaluation script (`scripts/run_validation.py`)

There is intentionally **no live trading, broker credential, or order-placement code** yet.

## Read-only Kite historical data

The repository now includes a read-only adapter in `src/agentic_investing/data/providers/kite.py` and a local fetch script at `scripts/fetch_kite_history.py`. The adapter has no order-placement methods.

Credentials are resolved automatically in this order:

1. `KITE_API_KEY` and `KITE_ACCESS_TOKEN` process environment variables, if both are set.
2. Otherwise, the saved local session from `scripts/kite_login.py` (see below), as long as it hasn't crossed the next daily 6 AM IST expiry.

Steps:

1. Install the official `kiteconnect` package in the project environment.
2. Run `scripts\kite_login.py` once per day (see "Local Kite login" below) — no manual environment variables are needed for the access token.
3. Obtain the correct instrument token from the current Zerodha instrument master; do not assume a token from an old file.
4. Run the fetch script for one small, approved historical date range first.
5. Review the generated JSON dataset and manifest under `data/real/`.

The script validates bars, records raw and normalized hashes, and writes no credentials beyond the session file already managed by the login flow. Never commit or paste API keys, secrets, or tokens into source files, notebooks, logs, or chat.

## Local Kite login

After setting `KITE_API_KEY` and the rotated `KITE_API_SECRET` in the current PowerShell process, run:

```text
\.venv\Scripts\python.exe scripts\kite_login.py
```

The script starts a local callback at `http://127.0.0.1:8765/kite-redirect`, opens the Zerodha login URL, and waits for you to complete login and TOTP in the browser. It exchanges the short-lived `request_token` for the daily `access_token`. The session is stored outside the repository under `%LOCALAPPDATA%\AgenticInvesting\kite-session.json`; the API secret is never stored. The callback URL in the Zerodha app must match exactly.

This flow only authenticates and stores a session. It does not place orders. Because Zerodha invalidates access tokens daily, rerun this script once per trading day before fetching data — `scripts/fetch_kite_history.py` will detect an expired session and tell you to rerun it.

## Run the foundation tests

From the repository root, run:

```text
C:/Users/muthusar/AppData/Local/Programs/Python/Python313/python.exe -m unittest discover -s tests -v
```

## Baseline backtest assumptions

The initial baseline is intentionally simple and is not a trading recommendation:

- Long-only, no leverage, one instrument at a time
- Signals use only closed bars
- Orders execute at the next bar's open
- Commission and slippage are configurable
- Positions are sized using the risk budget, stop-distance assumption, deployment limit, and available cash
- Invalid or look-ahead data is rejected

## Planned next milestone

Run these validation modules against properly sourced, authorized historical Indian-market data, review the results and robustness assumptions, then integrate the portfolio/risk engine more deeply before considering a paper broker or Zerodha adapter. See `IMPLEMENTATION_ROADMAP.md` for the complete phased plan.

The validation API is available from `agentic_investing.backtesting` through `evaluate_train_test`, `evaluate_walk_forward`, `run_cost_sensitivity`, and `render_validation_report`. The current repository deliberately uses offline fixtures only; no live or broker data is fetched automatically.

## Current free-data workflow

Use `scripts/fetch_yahoo_history.py` for exploratory research. Example symbols include `NIFTYBEES.NS`, `INFY.NS`, `TCS.NS`, and `^NSEI`.

```text
\.venv\Scripts\python.exe scripts/fetch_yahoo_history.py --symbol NIFTYBEES.NS --exchange NSE --timeframe 1d --start 2018-01-01 --end 2025-12-31
```

The script validates and fingerprints the downloaded data under `data/yahoo/`. Yahoo Finance is not broker-authoritative; verify licensing, corporate-action treatment, data completeness, and accuracy before relying on results. Keep the Kite provider for later comparison and broker-aligned validation. Do not use Yahoo data for live order decisions without an appropriate, authorized data source.

If Yahoo requests fail with `CertificateVerifyError`, do not disable TLS verification. Fix the local Python/Windows certificate trust chain or use an authorized alternative source. The ingestion layer rejects empty responses as invalid and will not create a usable manifest for a failed download.

## Safety

The 4% monthly return objective is not guaranteed and is not used as a required monthly quota. All future execution paths must pass deterministic risk controls and paper/shadow-trading gates before any live capital is considered.
