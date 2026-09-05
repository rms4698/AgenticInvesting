# Documentation Map

Each document has one job. Keep details in the narrowest appropriate source.

| File | Owns |
|---|---|
| `AGENTS.md` | Contributor and coding-agent rules: safety invariants, quality standards, prompt policy, logging, testing, and Git policy. |
| `README.md` | User-facing overview, current capabilities, setup, operating commands, and current limitations. |
| `IMPLEMENTATION_ROADMAP.md` | Planned phases, deliverables, exit gates, and sequencing. It is not a changelog. |
| `docs/prompts/*.md` | Runtime prompts and long-form model instructions. Prompt files are behavior, so review and test changes. |
| `docs/ai-cost-estimate.md` | AI-provider cost assumptions, formulas, and cost-control guidance. |
| `docs/zerodha-capability-matrix.md` | Zerodha/Kite capabilities, current integration status, and deliberate safety gaps. |
| `docs/decisions/` | Dated architectural and product decisions with rationale. |
| `reports/` | Generated validation and operator artifacts, not canonical design documentation. |

When the same fact appears in multiple places, keep the authoritative detail in the narrow document above and link to it elsewhere. Avoid copying full architecture descriptions, safety rules, or phase plans into multiple files.
