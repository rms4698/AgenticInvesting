"""Provider-neutral LLM clients for the research decision layer.

The trading framework owns a small structural protocol, ``ModelClient``.
The runner depends only on that protocol and normalized content blocks, never
on a vendor SDK. This keeps provider choice independent from risk/execution:
Claude can use Anthropic's native web-search server tool; OpenAI and DeepSeek
use the OpenAI-compatible chat-completions API and run with web search
 disabled unless a separate, provider-neutral search adapter is configured.

All providers still end at the same ``AgentRunner`` -> ``TradeProposal`` ->
``ProposalExecutor`` path. Changing a model provider cannot create a second
broker path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol, cast

import anthropic
import openai


@dataclass(frozen=True, slots=True)
class TextBlock:
    text: str
    type: str = "text"


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    content: list[Any]
    stop_reason: str | None


class ModelClient(Protocol):
    """Vendor-neutral model client used by ``AgentRunner``."""

    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: tuple[dict[str, Any], ...],
    ) -> Any: ...


class AnthropicModelClient:
    """Anthropic Messages API adapter, including native server tools."""

    def __init__(self, *, api_key: str | None = None) -> None:
        resolved_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not resolved_key:
            raise ValueError("ANTHROPIC_API_KEY is required for provider=claude")
        self._client = anthropic.Anthropic(api_key=resolved_key)

    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: tuple[dict[str, Any], ...],
    ) -> NormalizedMessage:
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=cast(Any, messages),
            tools=cast(Any, list(tools)),
        )
        return NormalizedMessage(
            content=list(response.content),
            stop_reason=str(response.stop_reason) if response.stop_reason is not None else None,
        )


class OpenAICompatibleModelClient:
    """Adapter for OpenAI-compatible APIs such as OpenAI, DeepSeek, Gemini, and Ollama.

    This adapter intentionally supports client-side function tools only. The
    runner must disable Anthropic's native web-search server tool for these
    providers; use the deterministic algorithm mode or add a separately
    configured search provider rather than pretending current web research
    happened when it did not.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
    ) -> None:
        resolved_key = api_key or os.environ.get(api_key_env)
        if not resolved_key:
            raise ValueError(f"{api_key_env} is required for this OpenAI-compatible provider")
        self._client = openai.OpenAI(api_key=resolved_key, base_url=base_url)

    def create_message(
        self,
        *,
        model: str,
        max_tokens: int,
        system: str,
        messages: list[dict[str, Any]],
        tools: tuple[dict[str, Any], ...],
    ) -> NormalizedMessage:
        openai_messages = _to_openai_messages(system, messages)
        function_tools = [_to_openai_function_tool(tool) for tool in tools if tool.get("type") != "web_search_20250305"]
        request: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "max_tokens": max_tokens,
        }
        if function_tools:
            request["tools"] = function_tools
        response = self._client.chat.completions.create(**cast(Any, request))
        choice = response.choices[0]
        content: list[Any] = []
        if choice.message.content:
            content.append(TextBlock(choice.message.content))
        for call in choice.message.tool_calls or ():
            function_call = cast(Any, call).function
            content.append(
                ToolUseBlock(
                    id=cast(Any, call).id,
                    name=function_call.name,
                    input=json.loads(function_call.arguments or "{}"),
                )
            )
        return NormalizedMessage(content=content, stop_reason=choice.finish_reason)


def _to_openai_function_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert the shared Anthropic/MCP schema to Chat Completions format."""

    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", tool["name"]),
            "parameters": tool["input_schema"],
        },
    }


def _to_openai_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        role = message["role"]
        content = message["content"]
        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(block.get("text", "") for block in content if block.get("type") == "text")
            tool_calls = [
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {"name": block["name"], "arguments": json.dumps(block["input"])},
                }
                for block in content
                if block.get("type") == "tool_use"
            ]
            converted.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls or None})
            continue
        if role == "user":
            for block in content:
                if block.get("type") == "tool_result":
                    converted.append(
                        {
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],
                            "content": block["content"],
                        }
                    )
                else:
                    converted.append({"role": "user", "content": json.dumps(block)})
            continue
        converted.append({"role": role, "content": content})
    return converted


def create_model_client(provider: str) -> ModelClient:
    """Create a configured provider client from a stable provider name.

    Supported names:
    - ``claude``: Anthropic Messages API and native web search.
    - ``openai``: OpenAI Chat Completions API.
    - ``deepseek``: DeepSeek's OpenAI-compatible endpoint.
    - ``gemini``: Google's Gemini OpenAI-compatible endpoint.
        - ``ollama``: local Ollama OpenAI-compatible endpoint; no API key or
            per-token billing, but requires a locally downloaded model.
    """

    normalized = provider.lower()
    if normalized == "claude":
        return AnthropicModelClient()
    if normalized == "openai":
        return OpenAICompatibleModelClient()
    if normalized == "deepseek":
        return OpenAICompatibleModelClient(
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        )
    if normalized == "gemini":
        return OpenAICompatibleModelClient(
            api_key=os.environ.get("GEMINI_API_KEY"),
            api_key_env="GEMINI_API_KEY",
            base_url=os.environ.get(
                "GEMINI_BASE_URL",
                "https://generativelanguage.googleapis.com/v1beta/openai/",
            ),
        )
    if normalized == "ollama":
        return OpenAICompatibleModelClient(
            api_key="ollama-local",
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        )
    raise ValueError(f"unsupported AI provider: {provider!r}")
