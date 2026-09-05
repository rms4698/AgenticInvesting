# Stage 2 — Deterministic Portfolio Layer

## Inputs

- Kite-authoritative daily OHLCV datasets, one JSON file per instrument.
- A versioned fundamentals JSON snapshot with `available_at` timestamps.
- Screening and risk configuration committed with the backtest run.

Fundamental snapshots use decimal strings and timezone-aware availability timestamps:

```json
[
  {
    "instrument": "RELIANCE",
    "exchange": "NSE",
    "available_at": "2025-01-02T12:00:00+05:30",
    "source": "approved-source-2025-01-02",
    "sector": "ENERGY",
    "market_cap": "1700000000000",
    "pe_ratio": "24.5",
    "revenue_growth": "0.08",
    "return_on_equity": "0.12",
    "debt_to_equity": "0.40"
  }
]
```

## Deterministic flow

1. Calculate local SMA, RSI, ATR, and volume-ratio features from bars through the decision bar only.
2. Reject fundamentals that were not available by the decision bar's `available_at` timestamp.
3. Apply liquidity and fundamental gates.
4. Rank passing candidates using technical and fundamental scores.
5. Limit selected candidates by maximum positions and portfolio risk.
6. Produce BUY/SELL/HOLD decisions.
7. Calculate stop-loss and target from ATR and minimum reward/risk.
8. Execute only through `OrderManager` and `PaperBroker` in backtests.

## Backtesting

`PortfolioBacktester` aligns instruments by timestamp, uses next-bar-open execution, checks stops/targets intrabar, and calculates portfolio metrics from the shared deterministic execution boundary. It is intentionally paper-only in this stage.

The current NIFTYBEES dataset contains one instrument, so a meaningful cross-sectional portfolio test requires adding more Kite datasets and an approved fundamentals snapshot. Until then, the single-instrument strategy comparison remains the valid research baseline.

## Acquisition workflow

- Universe policy is versioned in `config/portfolio_universe.json`. The default `all_equity` policy discovers current NSE cash-equity symbols from Kite; it is not a restriction to a small manual list.
- Run `scripts/fetch_portfolio_universe.py` after authenticating Kite to resolve symbols against the current instrument master and fetch a bounded validated batch through the existing ingestion path. Repeat batches deliberately rather than attempting an uncontrolled all-market download.
- Use `--offset` and `--limit` to continue through the discovered universe, for example `--offset 0 --limit 50`, then `--offset 50 --limit 50`.
- Put approved fundamentals snapshots under `data/fundamentals/`; the loader rejects naive timestamps, non-string decimal values, and duplicate instruments.
- The batch fetch requires a fresh Zerodha session. It is intentionally not run automatically and never places orders.
