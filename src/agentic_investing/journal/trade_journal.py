"""Trade journal: durable, per-instrument memory across agent runs.

This is deliberately NOT an agent. It is plain, deterministic, testable
Python that reads/writes a local SQLite database. The agent's reasoning
layer (``agentic_investing.agent``) is the only thing that decides *what* to
write; this module only knows how to persist and retrieve it reliably.

Schema is intentionally simple: one row per journal entry, one row per
per-instrument daily plan, keyed by (instrument, exchange). No ORM — this is
small enough that plain SQL is clearer and has zero extra dependencies.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def default_journal_path() -> Path:
    """Local, non-repository storage path, mirroring the Kite session convention."""

    import os

    root = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local"))
    return root / "AgenticInvesting" / "trade_journal.sqlite3"


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """One immutable, timestamped note about an instrument or the account overall."""

    id: int
    timestamp: datetime
    instrument: str | None  # None for account-level notes
    exchange: str | None
    category: str  # "ANALYSIS" | "DECISION" | "OUTCOME" | "EVENT" | "PLAN"
    message: str
    data: dict[str, Any]  # arbitrary structured payload (e.g. proposal, risk decision)


@dataclass(frozen=True, slots=True)
class DailyPlan:
    """The most recent plan recorded for one instrument, read back on the next run."""

    instrument: str
    exchange: str
    updated_at: datetime
    thesis: str
    target_price: str | None  # Decimal-as-string; journal never does financial math
    stop_price: str | None
    data: dict[str, Any]


class TradeJournal:
    """SQLite-backed durable memory for the agentic layer.

    Every write is timestamped in UTC and immediately committed — this
    journal is an audit trail, not a cache, and must survive process
    restarts exactly like the rest of this platform's persisted state
    (OrderStore, Kite session).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else default_journal_path()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the MCP tool server dispatches each sync
        # tool call onto a fresh worker thread (see anyio.to_thread), so a
        # single long-lived TradeJournal instance must be usable across
        # threads. This is safe here because calls are effectively
        # serialized in practice (one tool call completes before the next
        # begins) — this is not a concurrent-writer database.
        self._conn = sqlite3.connect(str(self._path), isolation_level=None, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                instrument TEXT,
                exchange TEXT,
                category TEXT NOT NULL,
                message TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_plans (
                instrument TEXT NOT NULL,
                exchange TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                thesis TEXT NOT NULL,
                target_price TEXT,
                stop_price TEXT,
                data TEXT NOT NULL,
                PRIMARY KEY (instrument, exchange)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entries_instrument ON entries(instrument, exchange, timestamp)"
        )

    def add_entry(
        self,
        *,
        category: str,
        message: str,
        instrument: str | None = None,
        exchange: str | None = None,
        data: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> int:
        """Append an immutable journal entry. Returns the new entry's id."""

        if not message.strip():
            raise ValueError("message must not be empty")
        moment = timestamp or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        cursor = self._conn.execute(
            "INSERT INTO entries (timestamp, instrument, exchange, category, message, data) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                moment.astimezone(timezone.utc).isoformat(),
                instrument,
                exchange,
                category,
                message.strip(),
                json.dumps(data or {}, sort_keys=True),
            ),
        )
        new_id = cursor.lastrowid
        if new_id is None:
            raise RuntimeError("sqlite did not return a row id for the inserted journal entry")
        return new_id

    def recent_entries(
        self,
        *,
        instrument: str | None = None,
        exchange: str | None = None,
        limit: int = 20,
    ) -> tuple[JournalEntry, ...]:
        """Return the most recent entries, optionally filtered to one instrument."""

        if limit <= 0:
            raise ValueError("limit must be positive")
        if instrument is not None:
            rows = self._conn.execute(
                "SELECT id, timestamp, instrument, exchange, category, message, data FROM entries "
                "WHERE instrument = ? AND exchange = ? ORDER BY id DESC LIMIT ?",
                (instrument, exchange, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, timestamp, instrument, exchange, category, message, data FROM entries "
                "ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    @staticmethod
    def _row_to_entry(row: tuple) -> JournalEntry:
        entry_id, timestamp_text, instrument, exchange, category, message, data_text = row
        return JournalEntry(
            id=entry_id,
            timestamp=datetime.fromisoformat(timestamp_text),
            instrument=instrument,
            exchange=exchange,
            category=category,
            message=message,
            data=json.loads(data_text),
        )

    def set_daily_plan(
        self,
        *,
        instrument: str,
        exchange: str,
        thesis: str,
        target_price: str | None = None,
        stop_price: str | None = None,
        data: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Overwrite the current plan for one instrument (upsert by instrument+exchange).

        Unlike journal entries, a plan is mutable current state, not an
        immutable log — each run's plan supersedes the previous one. The
        full history of *why* the plan changed still lives in journal
        entries, which are never overwritten.
        """

        if not thesis.strip():
            raise ValueError("thesis must not be empty")
        moment = timestamp or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        self._conn.execute(
            "INSERT INTO daily_plans (instrument, exchange, updated_at, thesis, target_price, stop_price, data) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(instrument, exchange) DO UPDATE SET "
            "updated_at=excluded.updated_at, thesis=excluded.thesis, "
            "target_price=excluded.target_price, stop_price=excluded.stop_price, data=excluded.data",
            (
                instrument,
                exchange,
                moment.astimezone(timezone.utc).isoformat(),
                thesis.strip(),
                target_price,
                stop_price,
                json.dumps(data or {}, sort_keys=True),
            ),
        )

    def get_daily_plan(self, *, instrument: str, exchange: str) -> DailyPlan | None:
        row = self._conn.execute(
            "SELECT instrument, exchange, updated_at, thesis, target_price, stop_price, data "
            "FROM daily_plans WHERE instrument = ? AND exchange = ?",
            (instrument, exchange),
        ).fetchone()
        if row is None:
            return None
        instrument_value, exchange_value, updated_at_text, thesis, target_price, stop_price, data_text = row
        return DailyPlan(
            instrument=instrument_value,
            exchange=exchange_value,
            updated_at=datetime.fromisoformat(updated_at_text),
            thesis=thesis,
            target_price=target_price,
            stop_price=stop_price,
            data=json.loads(data_text),
        )

    def all_daily_plans(self) -> tuple[DailyPlan, ...]:
        rows = self._conn.execute(
            "SELECT instrument, exchange, updated_at, thesis, target_price, stop_price, data FROM daily_plans"
        ).fetchall()
        plans = []
        for instrument_value, exchange_value, updated_at_text, thesis, target_price, stop_price, data_text in rows:
            plans.append(
                DailyPlan(
                    instrument=instrument_value,
                    exchange=exchange_value,
                    updated_at=datetime.fromisoformat(updated_at_text),
                    thesis=thesis,
                    target_price=target_price,
                    stop_price=stop_price,
                    data=json.loads(data_text),
                )
            )
        return tuple(plans)

    def close(self) -> None:
        self._conn.close()
