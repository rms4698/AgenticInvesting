"""Structured, source-aware web research collection for shortlisted stocks.

This module does not scrape websites. It delegates current web research to
Claude's native web-search server tool through the configured model client,
then validates and persists a small, timestamped fundamentals snapshot. It is
resumable: existing snapshots are skipped unless explicitly refreshed.

The model is never allowed to place an order. The output is research data
only and must pass schema/quality checks before it is stored.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

import anthropic

from agentic_investing.config import load_prompt


@dataclass(frozen=True, slots=True)
class ResearchSnapshot:
    instrument: str
    exchange: str
    available_at: datetime
    retrieved_at: datetime
    source: str
    source_urls: tuple[str, ...]
    sector: str
    market_cap: Decimal | None
    pe_ratio: Decimal | None
    revenue_growth: Decimal | None
    return_on_equity: Decimal | None
    debt_to_equity: Decimal | None
    confidence: str
    notes: str

    def to_json_record(self) -> dict[str, Any]:
        record = asdict(self)
        record["available_at"] = self.available_at.isoformat()
        record["retrieved_at"] = self.retrieved_at.isoformat()
        record["source_urls"] = list(self.source_urls)
        for field in ("market_cap", "pe_ratio", "revenue_growth", "return_on_equity", "debt_to_equity"):
            record[field] = str(getattr(self, field)) if getattr(self, field) is not None else None
        return record


class ResearchModelClient(Protocol):
    def research_fundamentals(self, *, instrument: str, exchange: str) -> dict[str, Any]: ...


class ClaudeWebResearchClient:
    """Structured research adapter backed by Claude native web search.

    The actual Anthropic call is intentionally isolated behind this protocol
    so collector tests never access the network.
    """

    def __init__(self, *, model: str = "claude-sonnet-4-5", max_searches: int = 5) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for web research")
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_searches = max_searches

    def research_fundamentals(self, *, instrument: str, exchange: str) -> dict[str, Any]:
        response = self._client.messages.create(
            model=self._model,
            max_tokens=2000,
            system=load_prompt("fundamentals_research.md"),
            messages=[
                {
                    "role": "user",
                    "content": f"Research {exchange}:{instrument}. Include source dates and URLs.",
                }
            ],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self._max_searches,
                }
            ],
        )
        text = next(
            (str(getattr(block, "text", "")) for block in response.content if getattr(block, "type", None) == "text"),
            "",
        )
        return json.loads(text)


def collect_snapshot(
    client: ResearchModelClient,
    *,
    instrument: str,
    exchange: str,
    retrieved_at: datetime | None = None,
) -> ResearchSnapshot:
    """Research, validate, and normalize one model response."""

    payload = client.research_fundamentals(instrument=instrument, exchange=exchange)
    source_urls = tuple(str(url) for url in payload.get("source_urls", []) if str(url).startswith("http"))
    confidence = str(payload.get("confidence", "LOW")).upper()
    available_at = _parse_timestamp(payload.get("available_at"))
    if not source_urls:
        raise ValueError(f"no verifiable source URLs returned for {exchange}:{instrument}")
    if confidence == "LOW":
        raise ValueError(f"research confidence is LOW for {exchange}:{instrument}")
    return ResearchSnapshot(
        instrument=instrument,
        exchange=exchange,
        available_at=available_at,
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
        source="claude_web_search",
        source_urls=source_urls,
        sector=str(payload.get("sector", "UNKNOWN")),
        market_cap=_decimal_or_none(payload.get("market_cap")),
        pe_ratio=_decimal_or_none(payload.get("pe_ratio")),
        revenue_growth=_decimal_or_none(payload.get("revenue_growth")),
        return_on_equity=_decimal_or_none(payload.get("return_on_equity")),
        debt_to_equity=_decimal_or_none(payload.get("debt_to_equity")),
        confidence=confidence,
        notes=str(payload.get("notes", "")),
    )


def append_snapshot(path: str | Path, snapshot: ResearchSnapshot) -> None:
    """Append one normalized snapshot atomically to a JSON list artifact."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if destination.exists():
        records = json.loads(destination.read_text(encoding="utf-8"))
    key = (snapshot.exchange, snapshot.instrument)
    replacement = snapshot.to_json_record()
    for index, row in enumerate(records):
        if (row.get("exchange"), row.get("instrument")) == key:
            records[index] = replacement
            break
    else:
        records.append(replacement)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(destination)


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("available_at must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("available_at must be timezone-aware")
    return parsed


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("research decimal fields must be strings or null")
    return Decimal(value)
