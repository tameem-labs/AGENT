"""Durable local Resource Manager with hard limits, queues, leases, and accounting."""

from __future__ import annotations

import sqlite3
from _thread import RLock
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Any, TypeVar, cast

from zyro.core.data import validate_text
from zyro.resources.contracts import (
    AdmissionOutcome,
    AdmissionResult,
    ConsumptionOutcome,
    RateLimitResult,
    ReservationStatus,
    ResourceKind,
    ResourcePolicy,
    ResourceReservation,
    TokenConsumptionResult,
    UsagePrecision,
    WorkLane,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


_ResultT = TypeVar("_ResultT")


def _serialized(method: Callable[..., _ResultT]) -> Callable[..., _ResultT]:
    @wraps(method)
    def wrapper(self: Any, *args: Any, **kwargs: Any) -> _ResultT:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class ResourceManagerError(RuntimeError):
    pass


class SQLiteResourceManager:
    """Controls whether capacity exists; it never decides what work should run."""

    def __init__(
        self,
        path: str | Path,
        policy: ResourcePolicy | None = None,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.path = Path(path)
        self.policy = policy or ResourcePolicy()
        self._clock = clock
        self._lock = RLock()
        try:
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS resource_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    resource_kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    workflow_id TEXT,
                    status TEXT NOT NULL,
                    acquired_at TEXT,
                    expires_at TEXT,
                    queued_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS token_usage (
                    scope_kind TEXT NOT NULL,
                    scope_id TEXT NOT NULL,
                    used INTEGER NOT NULL,
                    PRIMARY KEY(scope_kind,scope_id)
                );
                CREATE TABLE IF NOT EXISTS usage_events (
                    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    workflow_id TEXT,
                    agent_id TEXT,
                    consumer_id TEXT,
                    units INTEGER,
                    precision TEXT NOT NULL,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS resource_hard_stops (
                    stop_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    workflow_id TEXT,
                    attempted_units INTEGER,
                    task_used INTEGER NOT NULL,
                    task_limit INTEGER NOT NULL,
                    workflow_used INTEGER,
                    workflow_limit INTEGER,
                    reached_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rate_requests (
                    request_id TEXT PRIMARY KEY,
                    resource_id TEXT NOT NULL,
                    requested_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reservation_capacity
                    ON resource_reservations(resource_kind,resource_id,status,expires_at);
                CREATE INDEX IF NOT EXISTS idx_reservation_queue
                    ON resource_reservations(resource_kind,resource_id,status,lane,queued_at);
                CREATE INDEX IF NOT EXISTS idx_usage_task ON usage_events(task_id,recorded_at);
                CREATE INDEX IF NOT EXISTS idx_usage_workflow
                    ON usage_events(workflow_id,recorded_at);
                CREATE INDEX IF NOT EXISTS idx_rate_window
                    ON rate_requests(resource_id,requested_at);
                """
            )
            self._connection.execute("PRAGMA user_version=1")
            self._connection.commit()
            self.reconcile_leases()
        except sqlite3.Error as error:
            raise ResourceManagerError("unable to initialize resource manager") from error

    @_serialized
    def close(self) -> None:
        self._connection.close()

    @_serialized
    def reserve(
        self,
        reservation_id: str,
        owner_id: str,
        kind: ResourceKind,
        resource_id: str,
        lane: WorkLane,
        task_id: str,
        workflow_id: str | None = None,
        *,
        lease_seconds: float | None = None,
        queue_if_unavailable: bool = True,
    ) -> AdmissionResult:
        for name, value in (
            ("reservation_id", reservation_id),
            ("owner_id", owner_id),
            ("resource_id", resource_id),
            ("task_id", task_id),
        ):
            validate_text(value, name)
        now = self._clock()
        self.reconcile_leases()
        existing = self._row(reservation_id)
        if existing is not None:
            reservation = self._reservation(existing)
            if reservation.status is ReservationStatus.ACTIVE:
                outcome = AdmissionOutcome.ADMITTED
            elif reservation.status is ReservationStatus.QUEUED:
                outcome = AdmissionOutcome.QUEUED
            else:
                # Reservation IDs are durable idempotency keys. A terminal lease cannot
                # be silently revived or represented as queued; callers must use a new ID.
                outcome = AdmissionOutcome.LIMIT_REACHED
            return AdmissionResult(
                outcome,
                reservation,
                self.policy.concurrency_limit(reservation.resource_kind, reservation.resource_id),
            )
        limit = self.policy.concurrency_limit(kind, resource_id)
        active = self._active_count(kind, resource_id)
        admitted = active < limit
        status = ReservationStatus.ACTIVE if admitted else ReservationStatus.QUEUED
        if not admitted and not queue_if_unavailable:
            status = ReservationStatus.RELEASED
        seconds = self.policy.default_lease_seconds if lease_seconds is None else lease_seconds
        if seconds <= 0:
            raise ValueError("lease duration must be positive")
        acquired_at = now if admitted else None
        expires_at = now + timedelta(seconds=seconds) if admitted else None
        with self._connection:
            self._connection.execute(
                "INSERT INTO resource_reservations(reservation_id,owner_id,resource_kind,"
                "resource_id,lane,task_id,workflow_id,status,acquired_at,expires_at,queued_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    reservation_id,
                    owner_id,
                    kind.value,
                    resource_id,
                    lane.value,
                    task_id,
                    workflow_id,
                    status.value,
                    None if acquired_at is None else acquired_at.isoformat(),
                    None if expires_at is None else expires_at.isoformat(),
                    now.isoformat(),
                ),
            )
        reservation = self._reservation(self._row_required(reservation_id))
        outcome = (
            AdmissionOutcome.ADMITTED
            if admitted
            else (
                AdmissionOutcome.QUEUED if queue_if_unavailable else AdmissionOutcome.LIMIT_REACHED
            )
        )
        return AdmissionResult(outcome, reservation, limit)

    @_serialized
    def renew(
        self, reservation_id: str, owner_id: str, lease_seconds: float | None = None
    ) -> ResourceReservation:
        self.reconcile_leases()
        row = self._row_required(reservation_id)
        reservation = self._reservation(row)
        if reservation.owner_id != owner_id or reservation.status is not ReservationStatus.ACTIVE:
            raise ResourceManagerError("only the active lease owner may renew")
        seconds = self.policy.default_lease_seconds if lease_seconds is None else lease_seconds
        if seconds <= 0:
            raise ValueError("lease duration must be positive")
        expires = self._clock() + timedelta(seconds=seconds)
        with self._connection:
            self._connection.execute(
                "UPDATE resource_reservations SET expires_at=? WHERE reservation_id=?",
                (expires.isoformat(), reservation_id),
            )
        return self._reservation(self._row_required(reservation_id))

    @_serialized
    def release(self, reservation_id: str, owner_id: str) -> ResourceReservation:
        row = self._row_required(reservation_id)
        reservation = self._reservation(row)
        if reservation.owner_id != owner_id:
            raise ResourceManagerError("only the reservation owner may release")
        with self._connection:
            self._connection.execute(
                "UPDATE resource_reservations SET status=?,expires_at=NULL "
                "WHERE reservation_id=? AND status IN (?,?)",
                (
                    ReservationStatus.RELEASED.value,
                    reservation_id,
                    ReservationStatus.ACTIVE.value,
                    ReservationStatus.QUEUED.value,
                ),
            )
        self._promote(reservation.resource_kind, reservation.resource_id)
        return self._reservation(self._row_required(reservation_id))

    @_serialized
    def reconcile_leases(self) -> int:
        now = self._clock()
        rows = self._connection.execute(
            "SELECT DISTINCT resource_kind,resource_id FROM resource_reservations "
            "WHERE status=? AND expires_at<=?",
            (ReservationStatus.ACTIVE.value, now.isoformat()),
        ).fetchall()
        with self._connection:
            cursor = self._connection.execute(
                "UPDATE resource_reservations SET status=? WHERE status=? AND expires_at<=?",
                (
                    ReservationStatus.EXPIRED.value,
                    ReservationStatus.ACTIVE.value,
                    now.isoformat(),
                ),
            )
        for row in rows:
            self._promote(ResourceKind(row["resource_kind"]), row["resource_id"])
        return cursor.rowcount

    @_serialized
    def reservation(self, reservation_id: str) -> ResourceReservation:
        return self._reservation(self._row_required(reservation_id))

    @_serialized
    def consume_tokens(
        self,
        task_id: str,
        workflow_id: str | None,
        units: int | None,
        precision: UsagePrecision,
        *,
        agent_id: str | None = None,
        consumer_id: str | None = None,
        task_limit: int | None = None,
        workflow_limit: int | None = None,
    ) -> TokenConsumptionResult:
        validate_text(task_id, "task_id")
        if units is not None and units < 0:
            raise ValueError("token usage cannot be negative")
        if units is None and precision is not UsagePrecision.UNKNOWN:
            raise ValueError("unknown units require UNKNOWN precision")
        if units is not None and precision is UsagePrecision.UNKNOWN:
            raise ValueError("known units cannot use UNKNOWN precision")
        selected_task_limit = task_limit or self.policy.task_token_limit
        selected_workflow_limit = workflow_limit or self.policy.workflow_token_limit
        task_used = self._used("TASK", task_id)
        workflow_used = None if workflow_id is None else self._used("WORKFLOW", workflow_id)
        now = self._clock()
        if units is None:
            self._record_usage(task_id, workflow_id, None, precision, now, agent_id, consumer_id)
            return TokenConsumptionResult(
                ConsumptionOutcome.UNKNOWN_RECORDED,
                task_id,
                workflow_id,
                None,
                precision,
                task_used,
                selected_task_limit,
                workflow_used,
                None if workflow_id is None else selected_workflow_limit,
                max(0, selected_task_limit - task_used),
                None if workflow_used is None else max(0, selected_workflow_limit - workflow_used),
            )
        task_exceeded = task_used + units > selected_task_limit
        workflow_exceeded = (
            workflow_used is not None and workflow_used + units > selected_workflow_limit
        )
        if task_exceeded or workflow_exceeded:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO resource_hard_stops(task_id,workflow_id,attempted_units,task_used,"
                    "task_limit,workflow_used,workflow_limit,reached_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        task_id,
                        workflow_id,
                        units,
                        task_used,
                        selected_task_limit,
                        workflow_used,
                        None if workflow_id is None else selected_workflow_limit,
                        now.isoformat(),
                    ),
                )
            return TokenConsumptionResult(
                ConsumptionOutcome.RESOURCE_LIMIT_REACHED,
                task_id,
                workflow_id,
                units,
                precision,
                task_used,
                selected_task_limit,
                workflow_used,
                None if workflow_id is None else selected_workflow_limit,
                max(0, selected_task_limit - task_used),
                None if workflow_used is None else max(0, selected_workflow_limit - workflow_used),
            )
        with self._connection:
            self._set_used("TASK", task_id, task_used + units)
            if workflow_id is not None and workflow_used is not None:
                self._set_used("WORKFLOW", workflow_id, workflow_used + units)
            self._connection.execute(
                "INSERT INTO usage_events(task_id,workflow_id,agent_id,consumer_id,units,precision,"
                "recorded_at) VALUES(?,?,?,?,?,?,?)",
                (
                    task_id,
                    workflow_id,
                    agent_id,
                    consumer_id,
                    units,
                    precision.value,
                    now.isoformat(),
                ),
            )
        return TokenConsumptionResult(
            ConsumptionOutcome.ACCEPTED,
            task_id,
            workflow_id,
            units,
            precision,
            task_used + units,
            selected_task_limit,
            None if workflow_used is None else workflow_used + units,
            None if workflow_id is None else selected_workflow_limit,
            max(0, selected_task_limit - task_used - units),
            None
            if workflow_used is None
            else max(0, selected_workflow_limit - workflow_used - units),
        )

    @_serialized
    def check_rate_limit(self, request_id: str, resource_id: str) -> RateLimitResult:
        validate_text(request_id, "request_id")
        validate_text(resource_id, "resource_id")
        policy = self.policy.rate_limits.get(resource_id)
        if policy is None:
            raise ResourceManagerError("rate limit policy is not configured for resource")
        now = self._clock()
        window_start = now - timedelta(seconds=policy.window_seconds)
        existing = self._connection.execute(
            "SELECT requested_at FROM rate_requests WHERE request_id=? AND resource_id=?",
            (request_id, resource_id),
        ).fetchone()
        if existing is not None:
            used = self._connection.execute(
                "SELECT COUNT(*) AS count FROM rate_requests WHERE resource_id=? "
                "AND requested_at>?",
                (resource_id, window_start.isoformat()),
            ).fetchone()["count"]
            return RateLimitResult(True, resource_id, policy.max_requests, int(used))
        rows = self._connection.execute(
            "SELECT requested_at FROM rate_requests WHERE resource_id=? AND requested_at>? "
            "ORDER BY requested_at",
            (resource_id, window_start.isoformat()),
        ).fetchall()
        if len(rows) >= policy.max_requests:
            oldest = datetime.fromisoformat(rows[0]["requested_at"])
            retry_after = max(
                0.0, (oldest + timedelta(seconds=policy.window_seconds) - now).total_seconds()
            )
            return RateLimitResult(False, resource_id, policy.max_requests, len(rows), retry_after)
        try:
            with self._connection:
                self._connection.execute(
                    "INSERT INTO rate_requests(request_id,resource_id,requested_at) VALUES(?,?,?)",
                    (request_id, resource_id, now.isoformat()),
                )
        except sqlite3.IntegrityError:
            # Idempotent repeated admission for one request does not consume another unit.
            pass
        used = self._connection.execute(
            "SELECT COUNT(*) AS count FROM rate_requests WHERE resource_id=? AND requested_at>?",
            (resource_id, window_start.isoformat()),
        ).fetchone()["count"]
        return RateLimitResult(True, resource_id, policy.max_requests, used)

    @_serialized
    def hard_stop_count(self, task_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM resource_hard_stops WHERE task_id=?", (task_id,)
        ).fetchone()
        return int(row["count"])

    def _promote(self, kind: ResourceKind, resource_id: str) -> None:
        limit = self.policy.concurrency_limit(kind, resource_id)
        while self._active_count(kind, resource_id) < limit:
            now = self._clock()
            fairness_before = now - timedelta(seconds=self.policy.background_fairness_seconds)
            row = self._connection.execute(
                "SELECT * FROM resource_reservations WHERE resource_kind=? AND resource_id=? "
                "AND status=? ORDER BY CASE WHEN lane=? AND queued_at<=? THEN 0 "
                "WHEN lane=? THEN 1 ELSE 2 END,queued_at,reservation_id LIMIT 1",
                (
                    kind.value,
                    resource_id,
                    ReservationStatus.QUEUED.value,
                    WorkLane.BACKGROUND.value,
                    fairness_before.isoformat(),
                    WorkLane.INTERACTIVE.value,
                ),
            ).fetchone()
            if row is None:
                break
            expires = now + timedelta(seconds=self.policy.default_lease_seconds)
            with self._connection:
                self._connection.execute(
                    "UPDATE resource_reservations SET status=?,acquired_at=?,expires_at=? "
                    "WHERE reservation_id=? AND status=?",
                    (
                        ReservationStatus.ACTIVE.value,
                        now.isoformat(),
                        expires.isoformat(),
                        row["reservation_id"],
                        ReservationStatus.QUEUED.value,
                    ),
                )

    def _record_usage(
        self,
        task_id: str,
        workflow_id: str | None,
        units: int | None,
        precision: UsagePrecision,
        now: datetime,
        agent_id: str | None,
        consumer_id: str | None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO usage_events(task_id,workflow_id,agent_id,consumer_id,units,precision,"
                "recorded_at) VALUES(?,?,?,?,?,?,?)",
                (
                    task_id,
                    workflow_id,
                    agent_id,
                    consumer_id,
                    units,
                    precision.value,
                    now.isoformat(),
                ),
            )

    def _used(self, scope_kind: str, scope_id: str) -> int:
        row = self._connection.execute(
            "SELECT used FROM token_usage WHERE scope_kind=? AND scope_id=?",
            (scope_kind, scope_id),
        ).fetchone()
        return 0 if row is None else int(row["used"])

    def _set_used(self, scope_kind: str, scope_id: str, used: int) -> None:
        self._connection.execute(
            "INSERT INTO token_usage(scope_kind,scope_id,used) VALUES(?,?,?) "
            "ON CONFLICT(scope_kind,scope_id) DO UPDATE SET used=excluded.used",
            (scope_kind, scope_id, used),
        )

    def _active_count(self, kind: ResourceKind, resource_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) AS count FROM resource_reservations WHERE resource_kind=? "
            "AND resource_id=? AND status=?",
            (kind.value, resource_id, ReservationStatus.ACTIVE.value),
        ).fetchone()
        return int(row["count"])

    def _row(self, reservation_id: str) -> sqlite3.Row | None:
        return cast(
            sqlite3.Row | None,
            self._connection.execute(
                "SELECT * FROM resource_reservations WHERE reservation_id=?",
                (reservation_id,),
            ).fetchone(),
        )

    def _row_required(self, reservation_id: str) -> sqlite3.Row:
        row = self._row(reservation_id)
        if row is None:
            raise ResourceManagerError("resource reservation is not registered")
        return row

    @staticmethod
    def _reservation(row: sqlite3.Row) -> ResourceReservation:
        return ResourceReservation(
            row["reservation_id"],
            row["owner_id"],
            ResourceKind(row["resource_kind"]),
            row["resource_id"],
            WorkLane(row["lane"]),
            row["task_id"],
            row["workflow_id"],
            ReservationStatus(row["status"]),
            None if row["acquired_at"] is None else datetime.fromisoformat(row["acquired_at"]),
            None if row["expires_at"] is None else datetime.fromisoformat(row["expires_at"]),
            datetime.fromisoformat(row["queued_at"]),
        )


__all__ = ["ResourceManagerError", "SQLiteResourceManager"]
