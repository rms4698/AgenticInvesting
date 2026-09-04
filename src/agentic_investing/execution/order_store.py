"""Restart-safe, disk-persisted mapping from client order id to broker state.

Kite Connect has no client-supplied idempotency key for orders (only a short
"tag", max 20 alphanumeric characters). This store is what makes retries safe
across process restarts: before ever calling the broker, we persist that an
attempt is starting; only after a confirmed response do we mark it submitted.
If the process crashes in between, the adapter can consult the broker's order
book by tag to discover whether the order actually reached the exchange
before deciding whether a retry is safe.
"""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


def derive_tag(client_order_id: str) -> str:
    """Derive a Kite-compatible tag (alphanumeric, max 20 chars) for lookup.

    Uses a SHA-256 hash rather than truncating a sanitized client_order_id.
    Truncating after stripping non-alphanumerics can collide for genuinely
    different orders — e.g. two client_order_ids that differ only in their
    time component or a trailing "-buy"/"-sell" suffix beyond the 20th
    alphanumeric character would otherwise derive the identical tag. A
    hex-digest prefix is deterministic (same input always maps to the same
    tag, so restart recovery still works) and has negligible collision risk
    for the number of orders this platform will ever place.
    """

    if not client_order_id.strip():
        raise ValueError("client_order_id must not be empty")
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()
    return digest[:20]


@dataclass(slots=True)
class OrderStoreEntry:
    client_order_id: str
    tag: str
    stage: str  # "attempting" | "submitted" | "terminal"
    broker_order_id: str | None = None


class OrderStore:
    """JSON-file-backed idempotency store. Safe to share across restarts."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._entries: dict[str, OrderStoreEntry] = self._load()

    def _load(self) -> dict[str, OrderStoreEntry]:
        if not self._path.exists():
            return {}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        return {key: OrderStoreEntry(**value) for key, value in payload.items()}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: asdict(entry) for key, entry in self._entries.items()}
        temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(self._path)

    def get(self, client_order_id: str) -> OrderStoreEntry | None:
        return self._entries.get(client_order_id)

    def all_entries(self) -> dict[str, OrderStoreEntry]:
        """Return a copy of all tracked entries, keyed by client_order_id."""

        return dict(self._entries)

    def begin_attempt(self, client_order_id: str) -> OrderStoreEntry:
        """Persist that an order attempt is starting, before calling the broker."""

        entry = OrderStoreEntry(client_order_id, derive_tag(client_order_id), stage="attempting")
        self._entries[client_order_id] = entry
        self._save()
        return entry

    def mark_submitted(self, client_order_id: str, broker_order_id: str) -> None:
        entry = self._entries[client_order_id]
        entry.stage = "submitted"
        entry.broker_order_id = broker_order_id
        self._save()

    def mark_terminal(self, client_order_id: str) -> None:
        entry = self._entries[client_order_id]
        entry.stage = "terminal"
        self._save()
