# Daily Shadow-Trading Operator Runbook

Phase 6 (`IMPLEMENTATION_ROADMAP.md`) requires running shadow trading continuously
against live data over an extended observation period, with a daily operator
checklist and incident log, before any live-capital pilot is considered.

This is a manual daily procedure. It is **not** scheduled, because Zerodha
access tokens expire daily and require an interactive browser login — there
is no safe way to fully automate this without storing long-lived credentials.

## Daily checklist (run once after NSE market close, after 15:30 IST)

1. Open a terminal in the repository root and activate the virtual environment.
2. Run the daily update:

   ```text
   .\.venv\Scripts\python.exe scripts\run_daily_shadow_update.py --instrument-token 2707457 --symbol NIFTYBEES --exchange NSE --fast-period 20 --slow-period 50 --auto-login
   ```

   - `--auto-login` opens the Zerodha login page in your browser automatically
     if no fresh session exists; complete the login/TOTP there as usual.
   - Omit `--auto-login` if you prefer to run `scripts\kite_login.py` yourself first.
3. Confirm the command exits with code `0` and prints "Report archived to ...".
   - If it instead prints `Refusing to proceed: instrument_token ... maps to ...`,
     **stop** — do not re-run with a different token without first confirming it via
     `scripts\lookup_kite_instrument.py <symbol> <exchange>`. This guard exists
     because of a real incident (see `README.md`'s milestone log, 2026-09-04).
4. Open `reports\shadow_daily\latest.md` and review:
   - **Kill switch**: must read `clear`. If `TRIPPED`, stop daily automation
     consideration entirely and investigate before the next run.
   - **Orders blocked**: any blocked order should have an explainable reason
     (stale data, risk limit, insufficient cash) — investigate anything surprising.
   - **Incidents table**: review every new incident since the previous day's report.
     Holiday/weekend `DATA_GAP` entries are expected; anything else is not.
5. Append one line to `reports/shadow_daily/observation_log.md` (create it on day
   one) noting the date, whether anything was abnormal, and any manual action taken.
6. If anything looks wrong and you are unsure why, do not proceed to the next
   day's run until it is understood — per the risk charter's "fail closed" principle.

## What this procedure deliberately does NOT do

- It never places a real order. `ShadowTradingSession` always uses `PaperBroker`.
- It never touches `KiteBrokerAdapter`'s order-placement path.
- It does not persist shadow-session state between days; each run rebuilds the
  session from scratch and replays the full history, so a bad day's run cannot
  corrupt the next day's baseline (see `scripts/run_daily_shadow_update.py`'s
  module docstring for the rationale).

## Exit criteria before considering Phase 8 (small-capital pilot)

Per the roadmap, only proceed once:

- An extended period (covering multiple market regimes — do not shortcut this
  with a convenient short sample) of daily runs shows no unexplained order-state
  mismatches and no critical unresolved incidents.
- You can explain every entry in the observation log.
- The kill switch has never tripped unexpectedly, or if it has, the cause was
  understood and addressed.

A good backtest or shadow return alone is never sufficient justification to proceed.
