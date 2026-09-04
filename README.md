# Agentic Investing — India

Research-first, risk-controlled framework for Indian-market strategy research and staged Zerodha integration.

## Current milestone

This repository currently contains the Phase 0/Phase 1 foundation, Phase 2 data foundation, Phase 3 backtesting/validation foundation, Phase 4 deterministic risk engine, Phase 5 execution layer (including a real, not-yet-activated Zerodha broker adapter), and an initial Phase 6 shadow-trading capability:

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
- First real-data validation run: NIFTYBEES 2018-2025 via Kite, SMA(20,50) baseline (see `reports/niftybees_sma_20_50.md`)
- Automatic Kite historical-data date-range chunking (per-interval max-span limits)
- Kite instrument-token lookup script (`scripts/lookup_kite_instrument.py`)
- Session-aware `fetch_kite_history.py` (no manual access-token env var needed)
- End-to-end research evaluation script (`scripts/run_validation.py`)
- Standalone `RiskEngine`: mark-to-market drawdown tracking, hard-drawdown kill switch (with manual reset + audit log), daily/monthly loss-limit gating, max-open-position limit, and shared position sizing
- `Backtester` now delegates all sizing and pre-trade gating to `RiskEngine`, so backtests exercise the same deterministic risk decisions intended for later paper/live execution
- `PaperBroker`: in-memory simulated broker with idempotent orders, no-shorting/insufficient-cash rejection, and position tracking (no live orders, no broker credentials)
- `OrderManager`: the only sanctioned path from a trading decision to a broker call — every BUY is risk-checked and sized via `RiskEngine` before submission; SELL (position-closing) orders are never blocked by risk limits so capital can always exit a losing position
- Order-state reconciliation via `OrderManager.reconcile()` and the more thorough `reconcile_startup_state()` (orders, positions, and cash vs. expected state)
- `KiteBrokerAdapter`: places real CNC/MARKET orders via the official `kiteconnect` client, implementing the same `BrokerAdapter` protocol as `PaperBroker`. Uses a disk-persisted `OrderStore` plus Kite's order `tag` field for restart-safe idempotency — a retried `client_order_id` after a crash is recovered by scanning the broker's order book rather than blindly resubmitted.

**This adapter is not wired into any automated or scheduled workflow.** It has been exercised only against a fake Kite client in tests (`tests/test_kite_broker.py`) covering normal fills, rejections, network disconnects mid-placement, and restart recovery — never against a live Zerodha account. Using it against a real account is a deliberate, manual action outside this repository's automation, and should only follow paper/shadow trading per the roadmap.
- `ShadowTradingSession`: bar-by-bar shadow trading using the same `SmaCrossoverStrategy` + `RiskEngine` + `OrderManager` + `PaperBroker` stack as backtesting, but driven by a live/replayed bar feed instead of a single batch run. Detects data gaps exceeding a configurable tolerance and suppresses new BUY entries during them (SELL/exits are never suppressed); supports an explicit `mark_stale()` call for externally-detected outages (heartbeat, disconnect, token expiry). Produces a deterministic Markdown daily operator report (`daily_report()`) listing bars processed, cash/position/equity, orders submitted/blocked, kill-switch state, and a full incident log.
- **Position-aware signal generation**: `SmaCrossoverStrategy.decide(bars, index, holding=...)` makes every trading decision statelessly, grounded in the caller's *real* broker/portfolio position rather than an internally remembered belief. This closes a real desync risk: previously, if a proposed BUY was blocked (by risk limits, insufficient cash, or a data outage) the strategy could still believe it held a position and later miss a genuine buying opportunity. `Backtester` and `ShadowTradingSession` both call `decide()` per bar with the real position; `generate_signals()` remains available for direct strategy inspection but is no longer used by any execution path. Verified with dedicated regression tests (`tests/test_shadow_session.py::ShadowSessionPositionAwareDecisionTests`) proving a blocked BUY is correctly retried on the next bar.
- Replay script `scripts/run_shadow_replay.py` for sanity-checking the session against an already-fetched dataset (verified end-to-end against the real 1,982-bar NIFTYBEES dataset: 33 orders submitted, 0 blocked, 2 correctly detected holiday-gap incidents, kill switch clear)
- **Correctness audit and fixes** (`tests/test_risk_engine.py`, `tests/test_kite_broker.py`, `tests/test_shadow_session.py`, `tests/test_json_loader.py` contain dedicated regression tests for each): a systematic review for state-desync and correctness bugs across the risk/execution/shadow modules found and fixed 9 issues, including:
  - `RiskEngine.reset_kill_switch` was a mathematical no-op (re-baselined peak equity to `max(peak, daily_start)`, which can never change since peak is already the running maximum) — the kill switch would immediately re-trip on the next mark-to-market at the same depressed equity, defeating manual reset. Now re-baselines to current equity.
  - `KiteBrokerAdapter` recorded a `Fill` only once ever per order (`not order.fills` guard), so a later partial-to-full fill update from the broker was silently ignored, leaving `filled_quantity`/`average_fill_price` stale. Now resyncs fills to the broker's current cumulative state on every refresh.
  - `KiteBrokerAdapter.list_orders()` was backed only by an in-process cache, empty after every restart — `OrderManager.reconcile()`/`reconcile_startup_state()` would report zero issues even with real non-terminal orders at the broker. Added `hydrate()` to repopulate the cache from the broker's live order book (matched via the `OrderStore`'s tag mapping) before trading resumes.
  - `derive_tag()` truncated a sanitized `client_order_id` to 20 characters, which could collide for genuinely different orders (e.g. differing only in a trailing `-buy`/`-sell` suffix past the 20th alphanumeric character) — a collision could misattribute the wrong broker order during restart recovery. Now uses a SHA-256 hash prefix (still deterministic, no collision risk).
  - Fixed naive (timezone-unaware) `datetime.now()` usages in `ShadowTradingSession.mark_stale()` and `KiteBrokerAdapter`'s recovered-fill fallback, inconsistent with the tz-aware UTC convention used everywhere else in the system.
  - `data/json_loader.py` did not enforce timezone-aware timestamps, unlike the sibling CSV loader — a hand-edited or future-produced JSON file with naive timestamps would silently create naive `Bar` values. Now raises if a timestamp lacks a timezone offset.
  - `OrderManager._place`'s broad `except TypeError` (used to detect whether a broker's `place_order` accepted a simulated `fill_price`) could mask an unrelated `TypeError` raised inside a real broker adapter's implementation. Fixed at the root: `BrokerAdapter.place_order` now declares `fill_price`/`timestamp` explicitly in its signature, so no runtime introspection or exception-based detection is needed at all.
  - `ShadowTradingSession` had no guard against being fed bars for more than one instrument; `_current_position()` would silently return an arbitrary position. `on_bar()` now raises if a bar's instrument/exchange differs from the session's first bar.
  - `KiteBrokerAdapter`-built `Order`s never set `created_at`/`updated_at` (always `None`), unlike `PaperBroker`. Now stamped on every state transition.
- **Second correctness audit and fixes** (`tests/test_backtesting.py`, `tests/test_metrics.py`, `tests/test_ingestion.py`, `tests/test_json_loader.py`, `tests/test_kite_provider.py` contain dedicated regression tests for each): a follow-up systematic review found and fixed 9 further issues, including a critical look-ahead-bias defect that had gone undetected by the existing test suite and validation reports:
  - **Critical — look-ahead bias**: the Kite provider set a bar's `available_at` equal to its `timestamp` (the candle's *start*), asserting the entire candle's OHLC — including its close, which is only known once the interval ends — was knowable at the instant the interval began. `data/validation.py`'s `LOOKAHEAD_RISK` check only rejects `available_at < timestamp` (strictly earlier), so equal values silently passed. The provider now sets `available_at = timestamp + <interval duration>`.
  - **High — Backtester final-bar exit**: ending a backtest while still holding a position force-closed at the last bar's *open* price (rather than its close, inconsistent with every other exit) and appended a *second* equity-curve point for the same timestamp, corrupting period-return-based Sharpe/volatility. `_close_position()` now takes an explicit `at_open` keyword; the forced end-of-backtest exit uses the close with the same slippage as any other exit, and *replaces* rather than appends the final mark-to-market equity point.
  - **High — manifest hash never matched the on-disk file**: `normalized_sha256()` hashed a separately-constructed, differently-formatted payload (compact, sorted keys) than what was actually written to disk (indented, insertion order), so re-hashing a dataset file could never reproduce its own manifest's recorded digest — silently defeating the manifest's integrity-verification purpose. Both now share one `_serialize_bars()` function.
  - **High — non-atomic dataset/manifest writes**: a crash or killed process mid-write could leave a truncated file in place of a previously good dataset. Both now write to a `.tmp` sibling file and atomically rename it into place.
  - **Medium — silent float-precision loss in JSON ingestion**: `json_loader.py` parsed price fields via `Decimal(value)` regardless of JSON type, so a numeric (non-string) price field would silently inherit float binary-rounding error (`Decimal(100.1) != Decimal("100.1")`). Added `_parse_decimal()`, which requires price fields to be JSON strings.
  - **Medium — biased Sharpe/volatility**: `backtesting/metrics.py` used `statistics.pstdev` (population standard deviation, `N` denominator) instead of `statistics.stdev` (sample standard deviation, `N-1` denominator) — for the small samples typical of a backtest this systematically understates volatility and overstates the resulting Sharpe ratio. Now uses `stdev`. A non-positive equity-curve point (which should never occur given upstream cash/position guarantees) now raises rather than being silently skipped, which previously shrank the effective sample size with no signal that anomalous data had been dropped.
  - **Medium — overly broad exception handling in `kite_login.load_session()`**: a genuine `OSError` (permission denied, disk error, locked file) was caught by the same `except` clause as "no session file exists," silently returning `None` and hiding real I/O bugs. `OSError` now propagates; only the JSON/schema-shape exceptions are treated as "no valid session." Also added a timezone-awareness check on the loaded `generated_at`, consistent with the strict pattern used elsewhere.

There is intentionally **no automated live trading**. `KiteBrokerAdapter` can place real orders if a caller wires it to an authenticated `kiteconnect.KiteConnect` instance and calls it directly, but nothing in this repository does that automatically, on a schedule, or without explicit manual invocation.
- **Daily live shadow-trading driver** (`scripts/run_daily_shadow_update.py`): the first genuinely-live (not replayed) step of Phase 6. Run manually once after each trading day closes (Kite access tokens expire daily and require an interactive browser login, so this is deliberately not scheduled). Each run re-fetches the full historical range from Kite via the already-audited `ingest_historical_bars` path, then rebuilds a brand-new `ShadowTradingSession` from scratch and replays every bar in order, archiving a dated Markdown report under `reports/shadow_daily/` plus a `latest.md` pointer. Rebuilding from scratch every run — rather than persisting session state across days — makes it structurally impossible for this script to accumulate desynced state, matching the "ground truth over memory" principle already enforced inside `ShadowTradingSession` itself.
  - **Incident and fix**: before this script existed, a placeholder instrument token (`256265`, the NIFTY 50 *index*) was passed by mistake where the NIFTYBEES *ETF* token (`2707457`) was intended, silently overwriting the real validated dataset with wrong-instrument data (only caught by eyeballing an implausible index-level price scale). The dataset was restored byte-for-byte from a fresh correct fetch (confirmed via matching `raw_sha256`). `run_daily_shadow_update.py` now verifies the supplied `instrument_token` against Kite's live instrument master before fetching anything, refusing to proceed on a mismatch, and additionally backs up the existing dataset/manifest under `data/real/_backups/` before every overwrite.

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

Phase 5 (paper broker, risk-gated order manager, real Zerodha broker adapter, restart-safe idempotency, startup reconciliation) and Phase 6 shadow-trading (`ShadowTradingSession`, replayed against real historical NIFTYBEES data, and now `scripts/run_daily_shadow_update.py` for genuinely-live daily EOD runs) are implemented and tested. Remaining work before any live capital: accumulate an extended observation period of daily live shadow runs (per the roadmap's recommended review window) covering multiple market regimes, exercise planned-outage/reconnect scenarios in that live context, and only then consider a small-capital pilot per Phase 8. See `IMPLEMENTATION_ROADMAP.md` for the complete phased plan.

The validation API is available from `agentic_investing.backtesting` through `evaluate_train_test`, `evaluate_walk_forward`, `run_cost_sensitivity`, and `render_validation_report`. The risk engine is available from `agentic_investing.risk` through `RiskEngine`, `RiskDecision`, and `RiskLimits`. The execution layer is available from `agentic_investing.execution` through `OrderManager`, `PaperBroker`, `KiteBrokerAdapter`, `OrderStore`, `reconcile_startup_state`, and the `BrokerAdapter` protocol. Shadow trading is available from `agentic_investing.shadow` through `ShadowTradingSession`, `ShadowSessionConfig`, and `Incident`.

## Safety

The 4% monthly return objective is not guaranteed and is not used as a required monthly quota. All future execution paths must pass deterministic risk controls and paper/shadow-trading gates before any live capital is considered.
