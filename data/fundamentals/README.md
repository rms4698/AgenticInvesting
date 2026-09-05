# Versioned Fundamentals Snapshots

Place approved, source/date-aware fundamentals snapshots here before running a Stage 2 portfolio backtest.

Required fields are documented in `docs/portfolio-stage2.md`. Use decimal strings and timezone-aware `available_at` timestamps. Do not put unverified values here; missing or future fundamentals cause the screener to reject the instrument.

For no-key collection from approved public filings, prepare a reviewed JSON
manifest and run `scripts/collect_official_filings.py`. The collector stores
the normalized output here and archives the exact fetched documents under
`data/fundamentals/raw/`. See `docs/fundamentals-source-policy.md` for the
manifest contract and supported structured/PDF formats.
