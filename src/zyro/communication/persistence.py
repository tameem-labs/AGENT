"""SQLite persistence for durable communication delivery and recovery."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, cast
from uuid import uuid4

from zyro.communication.contracts import (
    AcknowledgementOutcome,
    DeadLetterRecord,
    DeliveryAttemptRecord,
    DeliveryRecord,
    DeliveryStatus,
    DirectMessage,
    EventAcknowledgement,
    EventSubscription,
    MessageDeliveryStatus,
)
from zyro.core.events import Event


class CommunicationPersistenceError(RuntimeError):
    """Sanitized persistence failure."""


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


class SQLiteCommunicationStore:
    """Single-process SQLite store; transactions make accepted publications durable."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self._connection = sqlite3.connect(self.path)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._create_schema()
        except sqlite3.Error as error:
            raise CommunicationPersistenceError(
                "unable to initialize communication store"
            ) from error

    def __enter__(self) -> SQLiteCommunicationStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._connection.close()

    def _create_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                envelope_json TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                response_json TEXT,
                error_code TEXT,
                error_type TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                idempotency_key TEXT NOT NULL UNIQUE,
                envelope_json TEXT NOT NULL,
                event_type TEXT NOT NULL,
                ordering_key TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS subscriptions (
                subscriber_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                PRIMARY KEY (subscriber_id, event_type)
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                delivery_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                subscriber_id TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                attempt_id TEXT,
                available_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_error_code TEXT,
                last_error_type TEXT,
                acked_at TEXT,
                UNIQUE (event_id, subscriber_id),
                FOREIGN KEY (event_id) REFERENCES events(event_id)
            );
            CREATE TABLE IF NOT EXISTS delivery_attempts (
                attempt_id TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                error_code TEXT,
                error_type TEXT,
                FOREIGN KEY (delivery_id) REFERENCES deliveries(delivery_id)
            );
            CREATE TABLE IF NOT EXISTS dead_letters (
                delivery_id TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                subscriber_id TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                failed_at TEXT NOT NULL,
                error_code TEXT NOT NULL,
                error_type TEXT NOT NULL,
                FOREIGN KEY (delivery_id) REFERENCES deliveries(delivery_id)
            );
            """
        )
        self._connection.commit()

    # Direct messages -------------------------------------------------
    def create_message(self, message: DirectMessage, now: datetime) -> bool:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO messages(message_id,envelope_json,status,updated_at) "
                    "VALUES(?,?,?,?)",
                    (
                        message.message_id,
                        message.to_json(),
                        "PENDING",
                        _iso(now),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as error:
            raise CommunicationPersistenceError("unable to persist direct message") from error

    def message_record(self, message_id: str) -> sqlite3.Row | None:
        try:
            return cast(
                sqlite3.Row | None,
                self._connection.execute(
                    "SELECT * FROM messages WHERE message_id=?", (message_id,)
                ).fetchone(),
            )
        except sqlite3.Error as error:
            raise CommunicationPersistenceError("unable to read direct message") from error

    def update_message(
        self,
        message_id: str,
        status: MessageDeliveryStatus,
        attempts: int,
        now: datetime,
        *,
        response: Mapping[str, Any] | None = None,
        error_code: str | None = None,
        error_type: str | None = None,
    ) -> None:
        response_json = None
        if response is not None:
            response_json = json.dumps(_plain(response), sort_keys=True, separators=(",", ":"))
        try:
            with self._connection:
                self._connection.execute(
                    "UPDATE messages SET status=?,attempts=?,response_json=?,error_code=?,"
                    "error_type=?,updated_at=? WHERE message_id=?",
                    (
                        status.value,
                        attempts,
                        response_json,
                        error_code,
                        error_type,
                        _iso(now),
                        message_id,
                    ),
                )
        except sqlite3.Error as error:
            raise CommunicationPersistenceError("unable to update direct message") from error

    # Durable events --------------------------------------------------
    def persist_event(self, event: Event, idempotency_key: str, now: datetime) -> bool:
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO events(event_id,idempotency_key,envelope_json,event_type,"
                    "ordering_key,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        event.event_id,
                        idempotency_key,
                        event.to_json(),
                        event.event_type,
                        event.delivery.ordering_key,
                        _iso(now),
                    ),
                )
                rows = self._connection.execute(
                    "SELECT subscriber_id FROM subscriptions WHERE event_type=? AND enabled=1",
                    (event.event_type,),
                ).fetchall()
                for row in rows:
                    self._insert_delivery(event.event_id, row["subscriber_id"], now)
            return True
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as error:
            raise CommunicationPersistenceError("unable to persist event") from error

    def register_subscription(
        self,
        subscription: EventSubscription,
        now: datetime,
    ) -> None:
        try:
            with self._connection:
                for event_type in subscription.event_types:
                    self._connection.execute(
                        "INSERT INTO subscriptions(subscriber_id,event_type,enabled) "
                        "VALUES(?,?,?) ON CONFLICT(subscriber_id,event_type) "
                        "DO UPDATE SET enabled=excluded.enabled",
                        (subscription.subscriber_id, event_type, int(subscription.enabled)),
                    )
                    if subscription.enabled:
                        events = self._connection.execute(
                            "SELECT event_id FROM events WHERE event_type=? ORDER BY sequence",
                            (event_type,),
                        ).fetchall()
                        for event in events:
                            self._insert_delivery(
                                event["event_id"],
                                subscription.subscriber_id,
                                now,
                            )
        except sqlite3.Error as error:
            raise CommunicationPersistenceError("unable to register event subscription") from error

    def _insert_delivery(self, event_id: str, subscriber_id: str, now: datetime) -> None:
        self._connection.execute(
            "INSERT OR IGNORE INTO deliveries(delivery_id,event_id,subscriber_id,status,"
            "available_at,updated_at) VALUES(?,?,?,?,?,?)",
            (
                f"{event_id}:{subscriber_id}",
                event_id,
                subscriber_id,
                DeliveryStatus.PENDING.value,
                _iso(now),
                _iso(now),
            ),
        )

    def event(self, event_id: str) -> Event:
        row = self._connection.execute(
            "SELECT envelope_json FROM events WHERE event_id=?", (event_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"event is not persisted: {event_id}")
        return Event.from_json(row["envelope_json"])

    def claim_next(
        self,
        now: datetime,
        available_subscribers: tuple[str, ...],
    ) -> tuple[Event, DeliveryRecord] | None:
        if not available_subscribers:
            return None
        placeholders = ",".join("?" for _ in available_subscribers)
        query = f"""
            SELECT d.*, e.sequence, e.envelope_json, e.ordering_key
            FROM deliveries d JOIN events e ON e.event_id=d.event_id
            WHERE d.status IN (?,?) AND d.available_at<=?
              AND d.subscriber_id IN ({placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM deliveries prior
                JOIN events pe ON pe.event_id=prior.event_id
                WHERE prior.subscriber_id=d.subscriber_id
                  AND e.ordering_key IS NOT NULL
                  AND pe.ordering_key=e.ordering_key
                  AND pe.sequence<e.sequence
                  AND prior.status NOT IN (?,?)
              )
            ORDER BY e.sequence, d.subscriber_id LIMIT 1
        """
        parameters: tuple[Any, ...] = (
            DeliveryStatus.PENDING.value,
            DeliveryStatus.RETRY_WAIT.value,
            _iso(now),
            *available_subscribers,
            DeliveryStatus.ACKED.value,
            DeliveryStatus.DEAD_LETTER.value,
        )
        try:
            with self._connection:
                row = self._connection.execute(query, parameters).fetchone()
                if row is None:
                    return None
                attempt = row["attempts"] + 1
                attempt_id = str(uuid4())
                self._connection.execute(
                    "UPDATE deliveries SET status=?,attempts=?,attempt_id=?,updated_at=? "
                    "WHERE delivery_id=? AND status IN (?,?)",
                    (
                        DeliveryStatus.DELIVERING.value,
                        attempt,
                        attempt_id,
                        _iso(now),
                        row["delivery_id"],
                        DeliveryStatus.PENDING.value,
                        DeliveryStatus.RETRY_WAIT.value,
                    ),
                )
                self._connection.execute(
                    "INSERT INTO delivery_attempts("
                    "attempt_id,delivery_id,attempt,status,started_at) VALUES(?,?,?,?,?)",
                    (attempt_id, row["delivery_id"], attempt, "DELIVERING", _iso(now)),
                )
            event = Event.from_json(row["envelope_json"])
            return event, DeliveryRecord(
                row["delivery_id"],
                row["event_id"],
                row["subscriber_id"],
                DeliveryStatus.DELIVERING,
                attempt,
                attempt_id,
                _time(row["available_at"]),
                now,
                row["last_error_code"],
                row["last_error_type"],
            )
        except sqlite3.Error as error:
            raise CommunicationPersistenceError("unable to claim event delivery") from error

    def acknowledge(
        self,
        acknowledgement: EventAcknowledgement,
    ) -> AcknowledgementOutcome:
        row = self._connection.execute(
            "SELECT * FROM deliveries WHERE event_id=? AND subscriber_id=?",
            (acknowledgement.event_id, acknowledgement.subscriber_id),
        ).fetchone()
        if row is None:
            return AcknowledgementOutcome.INVALID
        if row["status"] == DeliveryStatus.ACKED.value:
            return AcknowledgementOutcome.DUPLICATE
        if row["status"] != DeliveryStatus.DELIVERING.value:
            return AcknowledgementOutcome.STALE
        if row["attempt_id"] != acknowledgement.attempt_id:
            return AcknowledgementOutcome.INVALID
        with self._connection:
            self._connection.execute(
                "UPDATE deliveries SET status=?,acked_at=?,updated_at=? WHERE delivery_id=?",
                (
                    DeliveryStatus.ACKED.value,
                    _iso(acknowledgement.acknowledged_at),
                    _iso(acknowledgement.acknowledged_at),
                    row["delivery_id"],
                ),
            )
            self._connection.execute(
                "UPDATE delivery_attempts SET status='ACKED',completed_at=? WHERE attempt_id=?",
                (_iso(acknowledgement.acknowledged_at), acknowledgement.attempt_id),
            )
        return AcknowledgementOutcome.ACKED

    def fail_delivery(
        self,
        delivery_id: str,
        attempt_id: str,
        now: datetime,
        *,
        error_code: str,
        error_type: str,
        retryable: bool,
    ) -> DeliveryStatus:
        row = self._connection.execute(
            "SELECT d.*,e.envelope_json FROM deliveries d JOIN events e ON e.event_id=d.event_id "
            "WHERE d.delivery_id=?",
            (delivery_id,),
        ).fetchone()
        if row is None or row["status"] != DeliveryStatus.DELIVERING.value:
            return DeliveryStatus.DEAD_LETTER
        if row["attempt_id"] != attempt_id:
            return DeliveryStatus.DELIVERING
        event = Event.from_json(row["envelope_json"])
        exhausted = row["attempts"] >= event.delivery.retry_policy.max_attempts
        status = (
            DeliveryStatus.DEAD_LETTER if exhausted or not retryable else DeliveryStatus.RETRY_WAIT
        )
        available = now + timedelta(seconds=event.delivery.retry_policy.backoff_seconds)
        with self._connection:
            self._connection.execute(
                "UPDATE deliveries SET status=?,available_at=?,updated_at=?,last_error_code=?,"
                "last_error_type=? WHERE delivery_id=?",
                (
                    status.value,
                    _iso(available),
                    _iso(now),
                    error_code,
                    error_type,
                    delivery_id,
                ),
            )
            self._connection.execute(
                "UPDATE delivery_attempts SET status=?,completed_at=?,error_code=?,error_type=? "
                "WHERE attempt_id=?",
                (status.value, _iso(now), error_code, error_type, attempt_id),
            )
            if status is DeliveryStatus.DEAD_LETTER:
                self._connection.execute(
                    "INSERT OR REPLACE INTO dead_letters("
                    "delivery_id,event_id,subscriber_id,attempts,failed_at,error_code,error_type) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        delivery_id,
                        row["event_id"],
                        row["subscriber_id"],
                        row["attempts"],
                        _iso(now),
                        error_code,
                        error_type,
                    ),
                )
        return status

    def recover_inflight(self, now: datetime) -> int:
        rows = self._connection.execute(
            "SELECT delivery_id,attempt_id FROM deliveries WHERE status=?",
            (DeliveryStatus.DELIVERING.value,),
        ).fetchall()
        for row in rows:
            self.fail_delivery(
                row["delivery_id"],
                row["attempt_id"],
                now,
                error_code="delivery_interrupted",
                error_type="CrashRecovery",
                retryable=True,
            )
        return len(rows)

    def deliveries(self, event_id: str | None = None) -> tuple[DeliveryRecord, ...]:
        query = "SELECT * FROM deliveries"
        parameters: tuple[Any, ...] = ()
        if event_id is not None:
            query += " WHERE event_id=?"
            parameters = (event_id,)
        query += " ORDER BY delivery_id"
        rows = self._connection.execute(query, parameters).fetchall()
        return tuple(
            DeliveryRecord(
                row["delivery_id"],
                row["event_id"],
                row["subscriber_id"],
                DeliveryStatus(row["status"]),
                row["attempts"],
                row["attempt_id"],
                _time(row["available_at"]),
                _time(row["updated_at"]),
                row["last_error_code"],
                row["last_error_type"],
            )
            for row in rows
        )

    def attempts(self, delivery_id: str) -> tuple[DeliveryAttemptRecord, ...]:
        rows = self._connection.execute(
            "SELECT * FROM delivery_attempts WHERE delivery_id=? ORDER BY attempt",
            (delivery_id,),
        ).fetchall()
        return tuple(
            DeliveryAttemptRecord(
                row["attempt_id"],
                row["delivery_id"],
                row["attempt"],
                row["status"],
                _time(row["started_at"]),
                None if row["completed_at"] is None else _time(row["completed_at"]),
                row["error_code"],
                row["error_type"],
            )
            for row in rows
        )

    def dead_letters(self) -> tuple[DeadLetterRecord, ...]:
        rows = self._connection.execute(
            "SELECT dl.*,e.envelope_json FROM dead_letters dl "
            "JOIN events e ON e.event_id=dl.event_id ORDER BY dl.failed_at"
        ).fetchall()
        records: list[DeadLetterRecord] = []
        for row in rows:
            event = Event.from_json(row["envelope_json"])
            records.append(
                DeadLetterRecord(
                    row["delivery_id"],
                    row["event_id"],
                    row["subscriber_id"],
                    row["attempts"],
                    _time(row["failed_at"]),
                    row["error_code"],
                    row["error_type"],
                    event.request_id,
                    event.task_id,
                    event.workflow_id,
                    event.correlation_id,
                )
            )
        return tuple(records)

    def pending_without_handlers(self, available_subscribers: set[str]) -> tuple[str, ...]:
        rows = self._connection.execute(
            "SELECT DISTINCT subscriber_id FROM deliveries WHERE status IN (?,?)",
            (DeliveryStatus.PENDING.value, DeliveryStatus.RETRY_WAIT.value),
        ).fetchall()
        return tuple(
            sorted(
                row["subscriber_id"]
                for row in rows
                if row["subscriber_id"] not in available_subscribers
            )
        )


__all__ = ["CommunicationPersistenceError", "SQLiteCommunicationStore"]
