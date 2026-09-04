# Agentic Investing Platform — India

## Purpose

This document is the implementation reference for building a research-first, risk-controlled algorithmic investing and trading platform for Indian markets using a Zerodha account.

The target of **4% per month is an aspiration, not a guarantee or a safe planning assumption**. At monthly compounding, 4% is approximately 60% annualized before costs and taxes. The system must therefore prioritize capital preservation, measurable risk, reproducibility, and regulatory compliance over return chasing.

This roadmap deliberately separates:

- **Research** — generating and testing ideas
- **Decision support** — ranking validated opportunities
- **Risk control** — deterministic limits that agents cannot override
- **Execution** — submitting and managing broker orders
- **Operations** — monitoring, audit, incident response, and reporting

The first production version should not allow an LLM to place unrestricted orders or deploy untested strategy code.

---

## 1. Initial decisions and scope

Before implementation, record these decisions in `docs/decisions/`:

| Decision | Initial recommendation |
|---|---|
| Market | Indian NSE/BSE markets, subject to broker/API availability |
| Broker | Zerodha through the official Kite Connect APIs and approved workflows |
| Initial instruments | Cash equities and/or liquid index instruments; avoid beginning with complex options strategies |
| Initial mode | Historical research → paper trading/shadow mode → very small live capital |
| Strategy style | One simple, explainable baseline strategy before agents |
| Execution authority | Deterministic risk/execution service; human approval for new strategies and early live trades |
| Return objective | Research target only; no promise of 4% monthly performance |
| Primary constraint | Defined maximum drawdown and loss limits |
| Data policy | Versioned, timestamped data with no look-ahead or survivorship bias |

### Questions to answer before Phase 1

- What is the starting capital range?
- Which instruments are permitted: equities, ETFs, futures, or options?
- Will trading be intraday, positional, or both?
- What is the maximum acceptable portfolio drawdown?
- What is the maximum daily and monthly loss?
- Will the account be personal or used to provide signals/services to others?
- What are the tax, audit, and record-keeping requirements for the chosen activity?
- What operating hours and manual availability are realistic?

If the platform will manage money for others, sell signals, or provide investment advice, stop and obtain professional Indian regulatory and legal guidance before proceeding. Personal automation and services to third parties can have materially different obligations.

---

## 2. Guiding safety principles

1. **No guaranteed-return assumption.** A strategy that targets a return can still lose money.
2. **Risk before reward.** Define drawdown, leverage, exposure, and loss limits before optimizing returns.
3. **Agents propose; code and controls decide.** Natural-language output is never an execution authorization.
4. **Reproducibility.** Every signal must be traceable to a data snapshot, strategy version, and configuration.
5. **Paper first.** No live deployment until backtests, operational tests, and shadow trading meet predefined gates.
6. **Small initial capital.** Scale only after live behavior is consistent with validated expectations.
7. **Fail closed.** Missing data, stale prices, broker errors, uncertain positions, or breached limits must prevent new orders.
8. **Least privilege.** Keep API credentials, order permissions, and production deployment access restricted.
9. **Realistic evaluation.** Include brokerage, exchange charges, taxes, slippage, latency, STT, GST, stamp duty, SEBI charges, and other applicable costs using current authoritative schedules.
10. **Manual emergency control.** The operator must be able to cancel orders, disable new orders, and flatten positions where appropriate.

---

## 3. Target architecture

```text
Market data / broker data
          |
          v
  Data validation + storage  <----  Reference data / corporate actions
          |
          v
  Research + feature pipeline
          |
          +--> Baseline strategies --> Backtest / walk-forward engine
          |
          +--> Research agents --> Structured hypotheses/reports
                                      |
                                      v
                              Validation and approval gate
                                      |
                                      v
                             Strategy registry / versions
                                      |
                                      v
                         Signal service / portfolio allocator
                                      |
                                      v
                         Deterministic risk engine
                                      |
                          +-----------+-----------+
                          |                       |
                    Paper broker             Zerodha adapter
                          |                       |
                          +-----------+-----------+
                                      v
                          Orders, fills, positions
                                      |
                                      v
                         Reconciliation + monitoring
                                      |
                                      v
                           Dashboard, alerts, audit log
```

### Proposed technology baseline

- **Language:** Python for research and services
- **Research:** vectorbt, pandas/numpy, or QuantConnect LEAN; compare against a second implementation for critical results
- **Agent orchestration:** LangGraph or a small explicit workflow/state machine; do not begin with a free-form autonomous loop
- **Broker:** Official Zerodha Kite Connect APIs via a dedicated adapter; confirm current product, authentication, rate-limit, and automation requirements before implementation
- **Storage:** PostgreSQL for operational data; Parquet/object storage for versioned market-data snapshots
- **Messaging:** Start with a database-backed queue or Redis; add complexity only when required
- **API:** FastAPI for internal services and controls
- **UI:** Streamlit for the first operational dashboard, later replace with a dedicated frontend if needed
- **Deployment:** Docker Compose locally; a secured cloud or always-on host only after paper-trading stability
- **Testing:** pytest, property-based tests where valuable, contract tests for broker behavior, and replay tests using recorded market/order events
- **Observability:** structured logs, metrics, alerts, and immutable audit records

The exact stack can change, but the separation between research, risk, execution, and monitoring should remain.

---

# 4. Multi-phase implementation plan

## Phase 0 — Governance, goals, and risk charter

**Objective:** Define what the system is allowed to do before writing trading logic.

### Work items

- Create a written risk charter.
- Select the first market and instrument universe.
- Define trading style, holding period, and expected turnover.
- Define maximum risk per position, daily loss, monthly loss, leverage, and portfolio drawdown.
- Define behavior after a limit breach.
- Document whether the system is personal-use only or intended for external users.
- Confirm Zerodha account/API eligibility, permissions, costs, authentication flow, and current Kite Connect terms.
- Create a secrets policy: no credentials in source control, notebooks, logs, or prompts.
- Create an incident-response procedure and manual kill-switch procedure.

### Deliverables

- `docs/risk-charter.md`
- `docs/scope-and-assumptions.md`
- `docs/compliance-checklist.md`
- `docs/decisions/0001-initial-scope.md`
- Instrument allowlist and prohibited-instrument list

### Exit gate

No research or live order code proceeds until the maximum drawdown and loss limits are written down and accepted by the operator.

---

## Phase 1 — Repository and reproducible development foundation

**Objective:** Create a maintainable project that can reproduce every result.

### Work items

- Initialize the repository with clear modules:

```text
src/
  config/
  data/
  features/
  strategies/
  backtesting/
  portfolio/
  risk/
  execution/
  agents/
  monitoring/
  reporting/
tests/
docs/
notebooks/
infra/
```

- Add dependency management, formatting, linting, type checking, and tests.
- Add environment-specific configuration without committing secrets.
- Add Docker configuration for local services.
- Add CI checks for tests, linting, type checking, and security scans.
- Establish a versioning policy for strategies, datasets, and configuration.
- Add a common event model for market data, signals, orders, fills, positions, and risk decisions.

### Deliverables

- Running local project skeleton
- `README.md` with setup and operating instructions
- `.env.example` containing placeholders only
- Initial CI workflow
- Passing smoke test

### Exit gate

A new developer can clone the project, configure non-secret local settings, run tests, and reproduce a sample result.

---

## Phase 2 — Indian market data foundation

**Objective:** Build trustworthy, timestamp-aware data before strategy research.

### Work items

- Select licensed/authorized sources for historical and live data.
- Define canonical instrument identifiers, exchange, segment, expiry, strike, option type, and lot size fields.
- Store exchange timestamps and ingestion timestamps separately.
- Handle corporate actions and symbol changes.
- Record trading calendars, holidays, sessions, auction periods, and timezone rules.
- Add data-quality checks for duplicates, missing bars, impossible prices, stale quotes, and discontinuities.
- Prevent look-ahead bias by enforcing an `available_at` timestamp.
- Store raw data unchanged and create normalized derived tables.
- Capture bid/ask or a conservative spread model where available.

### Indian-market considerations

- NSE/BSE instrument masters can change; refresh and version them.
- Futures and options require correct expiry, strike, contract multiplier, lot size, margin, and settlement handling.
- Costs and taxes differ by product and transaction side; maintain a dated cost model rather than hard-coding old values.
- Do not assume that an OHLC backtest represents executable intraday fills.

### Deliverables

- Versioned raw and normalized datasets
- Data schema and data dictionary
- Data validation report
- Trading-calendar service
- Reproducible data download/ingestion job

### Exit gate

The same data snapshot produces the same validation report and backtest inputs. Known data defects are documented and either corrected or excluded.

---

## Phase 3 — Baseline strategy and backtesting engine

**Objective:** Establish a transparent benchmark without agents.

### Work items

- Choose one simple, explainable strategy.
- Implement signals as deterministic, testable code.
- Implement order timing precisely: signal time, order time, fill time, and next-bar assumptions.
- Model brokerage and all applicable charges using current parameters.
- Model spread, slippage, partial fills, rejected orders, and position limits.
- Add portfolio accounting, cash, realized/unrealized P&L, and mark-to-market rules.
- Add unit tests for entries, exits, sizing, reversals, gaps, and missing data.
- Run in-sample, out-of-sample, walk-forward, and stress tests.
- Compare results with a simple buy-and-hold or benchmark baseline.

### Required metrics

- Net return after modeled costs
- Annualized return and volatility
- Maximum drawdown and recovery time
- Sharpe and Sortino ratios
- Profit factor and expectancy
- Win/loss distribution
- Worst day, month, and losing streak
- Turnover, exposure, leverage, and margin usage
- Sensitivity to slippage, costs, parameter changes, and delayed fills

### Exit gate

The strategy must show robustness across unseen periods and reasonable cost/slippage assumptions. A high backtest return alone is not an approval criterion.

---

## Phase 4 — Portfolio, sizing, and deterministic risk engine

**Objective:** Ensure no strategy or agent can bypass account-level controls.

### Work items

- Implement position sizing based on predefined risk, volatility, liquidity, and portfolio exposure.
- Enforce maximum order value, position value, notional exposure, leverage, and concentration.
- Enforce maximum risk per trade, daily loss, monthly loss, and drawdown thresholds.
- Add correlation and aggregate exposure limits across strategies.
- Add market-hours, instrument-status, and stale-data checks.
- Add margin and available-cash checks before order submission.
- Define behavior for gaps, exchange halts, rejected orders, partial fills, and uncertain broker state.
- Add a kill switch that blocks new orders and raises an alert.
- Make risk decisions explainable and persist every decision.
- Keep risk rules independent of the LLM and strategy text.

### Deliverables

- Risk policy implementation
- Position-sizing module
- Pre-trade and post-trade checks
- Kill-switch and recovery runbook
- Risk-limit test suite

### Exit gate

Automated tests demonstrate that intentionally invalid orders are rejected and that limits remain enforced during failures, restarts, duplicate events, and stale data.

---

## Phase 5 — Zerodha integration in sandbox/paper mode

**Objective:** Validate broker connectivity and order-state handling without risking capital.

### Work items

- Register and configure the official Zerodha Kite Connect application as required.
- Implement secure login/session handling according to the current Zerodha workflow.
- Keep API keys and tokens outside source control and redact them from logs.
- Build a broker adapter with interfaces for:
  - instrument lookup
  - quotes and historical data where permitted
  - account balance and margins
  - order placement
  - order modification and cancellation
  - order history and fills
  - positions and holdings
- Implement idempotent client order identifiers and duplicate-order protection.
- Implement reconciliation: compare internal orders/positions with broker state at startup and periodically.
- Record every request, response, rejection, fill, and state transition.
- Use a paper broker or simulation adapter because live broker environments may not provide a full sandbox.
- Replay recorded broker events to test restarts and network failures.

### Exit gate

The adapter correctly handles normal orders, rejected orders, partial fills, cancellations, disconnects, retries, duplicate messages, and restart reconciliation without placing unintended live orders.

---

## Phase 6 — Paper trading and shadow operation

**Objective:** Test the complete system against live market conditions without live capital.

### Work items

- Run the strategy on live or delayed data with simulated fills.
- Compare expected fills with realistic spread and latency assumptions.
- Run the dashboard, alerts, reconciliation, and daily reports every trading day.
- Test planned outages, token expiry, process restarts, market-data gaps, and broker unavailability.
- Review every proposed order manually at the beginning.
- Track divergence between backtest, paper, and expected live behavior.
- Maintain a daily operator checklist and incident log.

### Minimum suggested observation period

Use a predefined period covering multiple market regimes rather than selecting a convenient short sample. The duration should be long enough to observe liquidity, volatility, gap, and operational failures.

### Exit gate

Paper trading meets predefined stability and risk criteria, with no unexplained order-state mismatches and no critical unresolved incidents.

---

## Phase 7 — Controlled agentic research layer

**Objective:** Add agents where they improve research and oversight without granting uncontrolled execution authority.

### Recommended agents

1. **Data-quality agent** — summarizes anomalies and proposes data checks.
2. **Market-regime agent** — classifies trend, volatility, liquidity, and event conditions using approved features.
3. **Hypothesis agent** — proposes testable strategy ideas in a strict schema.
4. **Backtest agent** — launches only approved, sandboxed experiments.
5. **Validation agent** — checks leakage, overfitting, sample size, costs, and out-of-sample performance.
6. **Risk-review agent** — explains exposures, drawdown, tail scenarios, and failure modes.
7. **Trade-review agent** — explains signals and flags deviations from the strategy specification.
8. **Operations agent** — summarizes system health, broker errors, and reconciliation status.

### Agent controls

- Use structured JSON outputs with schema validation.
- Give agents read-only access by default.
- Permit experiment execution only in isolated environments.
- Require human approval before registering a strategy for paper or live use.
- Require deterministic tests and risk checks before any order reaches the broker.
- Store prompts, model/version metadata, input references, output, and approval status.
- Detect prompt injection or untrusted text in external market/news inputs.
- Never place API credentials in agent context.
- Add budget, timeout, retry, and tool-permission limits.

### Exit gate

Agent output is useful, reproducible enough for review, and unable to bypass strategy registration, risk controls, or execution approvals.

---

## Phase 8 — Small-capital live pilot

**Objective:** Validate real execution with a deliberately limited amount of capital.

### Work items

- Start with the smallest practical capital and the least complex approved instrument set.
- Enable only one strategy and one broker account.
- Keep manual approval for new strategy versions and unusual orders.
- Set stricter pilot limits than the long-term limits.
- Monitor fills, slippage, costs, P&L, margin, and operational incidents daily.
- Reconcile broker positions before and after each session.
- Stop new trading automatically after risk-limit breaches or unexplained state mismatches.
- Maintain a dated release record for each live strategy version.

### Scale-up criteria

Scale only when all of the following are true:

- No critical operational incidents for the chosen review period.
- Live slippage and costs are consistent with the model.
- Risk limits have behaved as designed.
- Results are not dependent on a single exceptional trade or market regime.
- The operator can explain every live position and order.
- The strategy remains within its validated behavior envelope.

A strong return is not sufficient justification for increasing capital.

---

## Phase 9 — Production operations and ongoing review

**Objective:** Operate the platform safely over time.

### Daily controls

- Verify authentication, data freshness, market status, and system health.
- Check cash, margin, positions, orders, and reconciliation.
- Review risk dashboard and alerts.
- Confirm no unexpected strategy or configuration changes.
- Produce an end-of-day report with P&L, exposures, costs, and incidents.

### Weekly controls

- Review trades and execution quality.
- Review strategy drift, feature drift, and data-quality trends.
- Review agent outputs and false alerts.
- Confirm backups and restore procedures.
- Review open incidents and security logs.

### Monthly controls

- Compare realized results with validated expectations.
- Calculate returns after all known costs and taxes.
- Review maximum drawdown, risk-adjusted metrics, and concentration.
- Reassess whether the strategy still has a defensible rationale.
- Review Zerodha, exchange, API, and regulatory changes from authoritative sources.
- Decide whether to continue, reduce, pause, or retire each strategy.

### Retirement triggers

- Drawdown breach
- Persistent performance deterioration
- Unexpected exposure or execution behavior
- Data or broker integrity issue
- Material change in market structure, product rules, or API terms
- Strategy rationale no longer supported by evidence

---

# 5. Suggested first milestone sequence

Implement in this order:

1. Risk charter and instrument scope
2. Repository skeleton and event schemas
3. Versioned Indian-market data ingestion
4. One transparent baseline strategy
5. Backtesting with realistic costs and slippage
6. Portfolio accounting and deterministic risk engine
7. Paper broker and broker-state reconciliation
8. Live-data shadow trading
9. Zerodha adapter in non-live mode
10. Controlled research agents
11. Small-capital live pilot
12. Monitoring, review, and gradual scaling

Do not start by building a general-purpose autonomous agent. The highest-value early work is reliable data, realistic simulation, risk controls, and operational reconciliation.

---

# 6. Initial risk-policy template

Complete this before implementation. Values are placeholders and must be selected deliberately.

| Limit | Initial value | Action on breach |
|---|---:|---|
| Maximum risk per trade | `TBD` | Reject order |
| Maximum open portfolio risk | `TBD` | Reject order / reduce exposure |
| Maximum daily loss | `TBD` | Block new orders; notify operator |
| Maximum monthly loss | `TBD` | Pause strategy; review required |
| Maximum portfolio drawdown | `TBD` | Kill switch; manual approval to resume |
| Maximum leverage | `TBD` | Reject order |
| Maximum single-instrument exposure | `TBD` | Reject or resize order |
| Maximum strategy exposure | `TBD` | Reject or resize order |
| Maximum stale-data age | `TBD` | Block new orders |
| Maximum broker-state uncertainty | `0` | Block new orders and reconcile |
| Manual approval required above | `TBD` | Hold order for approval |

---

# 7. Definition of done for the first production release

The first release is complete only when:

- The strategy, data, configuration, and code versions are recorded for every signal and order.
- Backtests are reproducible and include realistic costs, slippage, and out-of-sample testing.
- Risk checks are deterministic, tested, and independent of agent output.
- Zerodha authentication and broker-state reconciliation are implemented securely.
- Paper trading has completed the predefined observation period.
- The kill switch and recovery procedure have been tested.
- Monitoring and alerts work during normal and failure scenarios.
- Secrets are protected and logs are redacted.
- Operational and regulatory assumptions have been reviewed for the actual use case.
- Live capital is limited and a manual operator can stop trading immediately.

---

## Important disclaimer

This roadmap is for software planning and general education. It is not investment, tax, legal, or regulatory advice, and it does not guarantee any return. Indian market rules, broker APIs, costs, product availability, and automation requirements can change. Verify current requirements with Zerodha, the relevant exchanges, SEBI, and qualified Indian professionals before using real capital or offering services to anyone else.
