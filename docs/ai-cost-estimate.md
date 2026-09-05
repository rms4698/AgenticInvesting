# AI Cost Estimate

## Bottom line

Your Zerodha Connect subscription is approximately **₹500/month**. That is separate from brokerage, exchange charges, taxes, and any applicable statutory costs when live orders are eventually placed.

For this framework, the safest cost baseline is:

| Mode | AI spend | Best use |
|---|---:|---|
| `algo` | ₹0 | Daily deterministic SMA/shadow baseline and cheap validation |
| `ai` with one instrument/day | roughly ₹100-₹500/month in a moderate workflow | Research and proposal generation while keeping the broker path unchanged |
| `ai` with five instruments/day | roughly ₹500-₹2,500/month, depending on searches, tokens, and provider | Broader watchlists after paper evidence justifies the cost |

These are planning ranges, not invoices. Provider prices, exchange data, token usage, taxes, and web-search usage can change. The runner records the number of client tool calls and web searches per instrument so actual usage can be measured before changing the budget.

## Calculation

Use this monthly estimate:

```text
monthly_ai_cost =
    trading_days * instruments * searches_per_run * search_price
  + trading_days * instruments * (
        input_tokens_per_run / 1,000,000 * input_price
      + output_tokens_per_run / 1,000,000 * output_price
    )
```

For Claude native web search, the documented planning price is about `$10 per 1,000 searches`, in addition to model token costs. A moderate one-instrument plan of 22 trading days, 2 searches/day, 8,000 input tokens, and 1,000 output tokens is approximately:

```text
22 * 2 * $0.01 = $0.44 search cost
plus model tokens
```

Convert USD to INR using the exchange rate at the time of budgeting. Do not hardcode the exchange rate into trading decisions.

OpenAI, DeepSeek, and Gemini use the same runner protocol through compatible chat-completions adapters. Their actual cost depends on the selected model and token usage. DeepSeek and Gemini often offer lower-cost or free-tier experimentation, but they do not provide Claude's native web-search server tool in this framework; current web research is therefore disabled unless a separate search provider is added.

`ollama` is the genuinely free-inference option: it runs a local model such as `qwen2.5:7b` on your machine through `http://localhost:11434/v1`. There is no per-token AI bill, but the tradeoff is local CPU/GPU/RAM usage, slower inference, model-download/storage cost, and no native web search. A local model should begin in advisory/paper mode and should prefer `HOLD` whenever current research is needed but unavailable.

## Cost controls that do not weaken trading safety

- Start with `--mode algo` for daily operation and run AI research only once per day on a very small watchlist.
- Keep `max_web_searches` capped per instrument.
- Use a cheaper model for research and reserve a stronger model for a second-pass review only when the algorithm detects a candidate.
- Reuse the journal and daily plan instead of asking the model to re-research unchanged facts.
- Cache immutable source results with timestamps and source URLs; never reuse stale prices or corporate-action facts as current data.
- Keep AI in proposal mode. Lowering AI spend must never remove deterministic stops, risk checks, reconciliation, or exit capability.
- Measure actual tokens/searches from reports before expanding the watchlist.

## Does algorithm-first make more sense?

Yes. The deterministic algorithm should be the first daily operating mode because it is free, reproducible, backtestable, and provides a baseline against which AI decisions can be evaluated. AI should initially be an advisory/research layer, not the only source of trades.

The framework should retain an explicit mode switch:

- `algo`: deterministic SMA/shadow path.
- `ai`: provider-selected research and proposal path.

Both modes must continue through the same `RiskEngine`, `OrderManager`, and paper broker. A future `hybrid` mode can let the algorithm nominate candidates while AI performs research, but it must still produce only one structured proposal and must never create a second execution path.
