# Prompt Catalog

Prompts and long-form instructions are stored as Markdown files rather than embedded in Python source.

- `agent_system.md` — Claude reasoning and execution-boundary instructions.
- `mcp_server_instructions.md` — MCP server safety instructions.

These files are versioned with the application and loaded at runtime. Changes to instructions should be reviewed like code because they can change agent behavior.
