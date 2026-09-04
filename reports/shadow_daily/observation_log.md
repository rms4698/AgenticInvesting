# Shadow-Trading Observation Log

One line per daily run. Reviewed per `docs/daily-shadow-runbook.md`.

| Date | Kill switch | Orders blocked | New incidents | Notes |
|---|---|---:|---|---|
| 2026-09-04 | clear | 0 | none (beyond expected holiday gaps) | First live daily run after building `run_daily_shadow_update.py`. Dataset extended from 1,982 to 2,150 bars (2018-01-01 to 2026-09-03). An earlier test run used the wrong instrument token (NIFTY 50 index, not NIFTYBEES ETF) and was caught and fully repaired before this entry; see `README.md` milestone log. |
| 2026-09-04 | clear | 0 | 2 STOP_LOSS, 2 TARGET_EXIT (across full 2018-2026 replay, not just today) | Added independent per-position stop-loss (20% below entry) and profit-target (30% above entry, `minimum_reward_risk`=1.5x the stop) exits, checked every bar via intrabar high/low — previously the only exit was the lagging SMA crossover SELL signal with no hard downside cap. A naive reuse of the existing 5% sizing-only `stop_distance_fraction` as the stop trigger caused 81 whipsaw stop-outs and turned +9.68% into -2.95% on the full NIFTYBEES backtest; fixed by decoupling into a new `stop_loss_distance_fraction` (20% default, chosen via walk-forward sensitivity check, not full-sample return maximization — see `README.md` for the full writeup). Re-verified end-to-end via this daily run and `run_validation.py`. |
