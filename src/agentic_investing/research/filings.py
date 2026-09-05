"""No-key ingestion of explicitly approved official filing documents.

The collector is manifest-driven and intentionally conservative. It fetches
only HTTPS URLs classified as NSE, BSE, or a manually approved company IR
source. Structured JSON, XML/XBRL-style tags, and CSV facts are supported;
PDF and arbitrary HTML are rejected because extracting values from them
without a reviewed parser would create unverifiable fundamentals.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse

import pypdf

from agentic_investing.portfolio.models import FundamentalSnapshot


_ALLOWED_SOURCE_KINDS = {"nse", "bse", "company_ir"}
_METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "market_cap": ("market_cap", "marketcapitalization", "marketcapitalisation"),
    "pe_ratio": ("pe_ratio", "peratio", "priceearningsratio"),
    "revenue_growth": ("revenue_growth", "revenuegrowth"),
    "return_on_equity": ("return_on_equity", "returnonequity", "roe"),
    "debt_to_equity": ("debt_to_equity", "debtequity", "debttoequity", "debt_equity_ratio"),
}


@dataclass(frozen=True, slots=True)
class FilingManifestEntry:
    instrument: str
    exchange: str
    source_kind: str
    source_url: str
    available_at: datetime
    sector: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.source_kind not in _ALLOWED_SOURCE_KINDS:
            raise ValueError(f"unsupported official filing source kind: {self.source_kind}")
        _validate_source_url(self.source_url, self.source_kind)
        if self.available_at.tzinfo is None:
            raise ValueError("filing available_at must be timezone-aware")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> FilingManifestEntry:
        return cls(
            instrument=str(value["instrument"]),
            exchange=str(value["exchange"]).upper(),
            source_kind=str(value["source_kind"]),
            source_url=str(value["source_url"]),
            available_at=_parse_timestamp(value["available_at"]),
            sector=str(value.get("sector", "UNKNOWN")),
        )


@dataclass(frozen=True, slots=True)
class FetchedFiling:
    body: bytes
    content_type: str


class FilingFetcher(Protocol):
    def fetch(self, url: str) -> FetchedFiling: ...


class UrlLibFilingFetcher:
    """Small standard-library fetcher with bounded, auditable downloads."""

    def __init__(self, *, timeout: float = 30.0, max_bytes: int = 10_000_000) -> None:
        self._timeout = timeout
        self._max_bytes = max_bytes

    def fetch(self, url: str) -> FetchedFiling:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json, application/xml, text/xml, text/csv",
                "User-Agent": "agentic-investing-official-filings/0.1",
            },
        )
        with urllib.request.urlopen(request, timeout=self._timeout) as response:
            body = response.read(self._max_bytes + 1)
            if len(body) > self._max_bytes:
                raise ValueError(f"official filing exceeds {self._max_bytes} byte limit")
            return FetchedFiling(body=body, content_type=response.headers.get_content_type())


@dataclass(frozen=True, slots=True)
class OfficialFilingSnapshot:
    fundamentals: FundamentalSnapshot
    source_url: str
    retrieved_at: datetime
    document_sha256: str
    raw_body: bytes

    def to_json_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "instrument": self.fundamentals.instrument,
            "exchange": self.fundamentals.exchange,
            "available_at": self.fundamentals.available_at.isoformat(),
            "source": self.fundamentals.source,
            "source_url": self.source_url,
            "retrieved_at": self.retrieved_at.isoformat(),
            "document_sha256": self.document_sha256,
            "sector": self.fundamentals.sector,
        }
        for field in ("market_cap", "pe_ratio", "revenue_growth", "return_on_equity", "debt_to_equity"):
            value = getattr(self.fundamentals, field)
            record[field] = str(value) if value is not None else None
        return record


def collect_official_filing(
    entry: FilingManifestEntry,
    fetcher: FilingFetcher,
    *,
    retrieved_at: datetime | None = None,
) -> OfficialFilingSnapshot:
    """Fetch and parse one manifest entry into a validated fundamentals snapshot."""

    fetched = fetcher.fetch(entry.source_url)
    facts = _parse_facts(fetched.body, fetched.content_type)
    source = f"official_filing:{entry.source_kind}"
    fundamentals = FundamentalSnapshot(
        instrument=entry.instrument,
        exchange=entry.exchange,
        available_at=entry.available_at,
        source=source,
        sector=entry.sector,
        market_cap=_decimal_or_none(facts.get("market_cap")),
        pe_ratio=_decimal_or_none(facts.get("pe_ratio")),
        revenue_growth=_decimal_or_none(facts.get("revenue_growth")),
        return_on_equity=_decimal_or_none(facts.get("return_on_equity")),
        debt_to_equity=_decimal_or_none(facts.get("debt_to_equity")),
    )
    return OfficialFilingSnapshot(
        fundamentals=fundamentals,
        source_url=entry.source_url,
        retrieved_at=retrieved_at or datetime.now(timezone.utc),
        document_sha256=hashlib.sha256(fetched.body).hexdigest(),
        raw_body=fetched.body,
    )


def write_snapshot(path: str | Path, snapshot: OfficialFilingSnapshot) -> None:
    """Atomically replace the current snapshot for one instrument."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    if destination.exists():
        payload = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("fundamentals snapshot file must contain a list")
        records = payload
    replacement = snapshot.to_json_record()
    key = (snapshot.fundamentals.exchange, snapshot.fundamentals.instrument)
    for index, row in enumerate(records):
        if (row.get("exchange"), row.get("instrument")) == key:
            records[index] = replacement
            break
    else:
        records.append(replacement)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(destination)


def _parse_facts(body: bytes, content_type: str) -> dict[str, Any]:
    normalized_type = content_type.lower()
    if "pdf" in normalized_type or body.startswith(b"%PDF"):
        return _facts_from_pdf(body)
    text = body.decode("utf-8-sig")
    stripped = text.lstrip()
    if "json" in normalized_type or stripped.startswith(("{", "[")):
        return _facts_from_json(json.loads(text))
    if "xml" in normalized_type or stripped.startswith("<"):
        return _facts_from_xml(ElementTree.fromstring(text))
    if "csv" in normalized_type or _looks_like_csv(text):
        return _facts_from_csv(text)
    raise ValueError(f"unsupported official filing content type: {content_type or 'unknown'}")


def _facts_from_json(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("official filing JSON must contain an object")
    facts = payload.get("facts", payload)
    if not isinstance(facts, Mapping):
        raise ValueError("official filing JSON facts must be an object")
    return _normalized_facts(facts)


def _facts_from_xml(root: ElementTree.Element) -> dict[str, Any]:
    found: dict[str, list[str]] = {}
    for element in root.iter():
        name = _normalize_name(element.tag.rsplit("}", 1)[-1])
        metric = _metric_for_name(name)
        if metric and element.text and element.text.strip():
            found.setdefault(metric, []).append(element.text.strip())
    return _reject_conflicts(found)


def _facts_from_csv(text: str) -> dict[str, Any]:
    rows = csv.DictReader(io.StringIO(text))
    found: dict[str, list[str]] = {}
    for row in rows:
        metric_value = row.get("metric") or row.get("field") or row.get("name")
        value = row.get("value")
        if metric_value and value not in (None, ""):
            metric = _metric_for_name(_normalize_name(metric_value))
            if metric:
                found.setdefault(metric, []).append(value.strip())
    return _reject_conflicts(found)


def _facts_from_pdf(body: bytes) -> dict[str, Any]:
    reader = pypdf.PdfReader(io.BytesIO(body))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return _facts_from_pdf_text(text)


def _facts_from_pdf_text(text: str) -> dict[str, Any]:
    """Extract only a consolidated revenue-growth headline from PDF text.

    Annual-result PDFs have many segment tables with repeated labels. The
    consolidated headline is the only ratio extracted here until a reviewed
    issuer-specific table parser exists; all other metrics remain unavailable.
    """

    match = re.search(
        r"Consolidated\s+Revenue\s+at.*?up\s+([0-9]+(?:\.[0-9]+)?)%\s+Y\s*-?\s*o\s*-?\s*Y",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {}
    growth = Decimal(match.group(1)) / Decimal("100")
    return {"revenue_growth": str(growth)}


def _normalized_facts(facts: Mapping[str, Any]) -> dict[str, Any]:
    found: dict[str, list[str]] = {}
    for name, value in facts.items():
        metric = _metric_for_name(_normalize_name(str(name)))
        if metric and value not in (None, ""):
            values = value if isinstance(value, list) else [value]
            found.setdefault(metric, []).extend(str(item) for item in values if item not in (None, ""))
    return _reject_conflicts(found)


def _reject_conflicts(found: Mapping[str, list[str]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for metric, values in found.items():
        normalized = {_decimal_text(value) for value in values}
        if len(normalized) > 1:
            raise ValueError(f"conflicting official filing values for {metric}")
        result[metric] = values[0]
    return result


def _metric_for_name(name: str) -> str | None:
    for metric, aliases in _METRIC_ALIASES.items():
        if name in {_normalize_name(alias) for alias in aliases}:
            return metric
    return None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        value = str(value)
    try:
        return Decimal(value.replace(",", "").strip())
    except InvalidOperation as error:
        raise ValueError(f"official filing metric is not a decimal: {value!r}") from error


def _decimal_text(value: str) -> str:
    decimal = _decimal_or_none(value)
    return str(decimal) if decimal is not None else ""


def _normalize_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _looks_like_csv(text: str) -> bool:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    return "," in first_line and any(header in first_line.lower() for header in ("metric", "field", "name"))


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("filing available_at must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("filing available_at must be timezone-aware")
    return parsed


def _validate_source_url(url: str, source_kind: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("official filing source_url must be an HTTPS URL")
    host = parsed.hostname.lower()
    if source_kind == "nse" and not (host == "nseindia.com" or host.endswith(".nseindia.com")):
        raise ValueError("NSE filing URL must use nseindia.com")
    if source_kind == "bse" and not (host == "bseindia.com" or host.endswith(".bseindia.com")):
        raise ValueError("BSE filing URL must use bseindia.com")
