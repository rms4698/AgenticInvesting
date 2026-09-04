# Initial Risk Charter

**Status:** Agreed starting profile  
**Account capital:** ₹1,00,000  
**Use:** Personal research and staged paper/shadow trading  
**Broker:** Zerodha, with live integration deferred until later phases

## Scope

- Indian NSE cash equities and liquid ETFs
- Positional/swing trading
- No leverage initially
- No futures or options initially
- Maximum deployment of 80% of account capital; retain cash reserve
- Historical research before paper/shadow trading
- LLM agents restricted to research, validation, monitoring, and reporting

## Limits

| Limit | Starting value | System response |
|---|---:|---|
| Maximum deployed capital | ₹80,000 | Reject orders exceeding available deployment budget |
| Risk per trade | ₹500 (0.5%) | Reject or resize order |
| Maximum open portfolio risk | ₹2,000 (2%) | Reject or resize order |
| Maximum single position | ₹15,000 (15%) | Reject or resize order |
| Maximum sector exposure | 25% | Reject or resize order |
| Maximum open positions | 8 | Reject new position |
| Maximum leverage | 1× | Reject leveraged order |
| Daily loss limit | ₹1,000 (1%) | Block new orders and alert operator |
| Monthly loss limit | ₹5,000 (5%) | Pause strategy and require review |
| Drawdown review threshold | 8% | Require manual review and reduce risk |
| Hard drawdown limit | 12% | Kill switch; no new orders until manual approval |
| Minimum preferred reward/risk | 1.5:1 | Reject or flag candidate trade |

## Operating rules

- These limits are initial configuration, not performance promises.
- The system must use the smallest applicable limit when sizing a position.
- Risk calculations must include realistic costs and slippage where known.
- Uncertain broker state, stale data, missing prices, or failed reconciliation blocks new orders.
- The risk engine is deterministic and independent from any LLM.
- No live order placement is permitted in the current milestone.
- Any change to these limits requires a dated decision record and updated tests.

## Review triggers

Review this charter after backtesting, paper trading, material changes in capital, a drawdown breach, a strategy change, or changes to Indian-market/broker requirements.

This document is a software risk-control specification, not investment, tax, legal, or regulatory advice.
