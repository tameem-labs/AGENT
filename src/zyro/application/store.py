"""Application-facing chat and Task projection store for the localhost product."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _now() -> datetime:
    return datetime.now(UTC)


class ApplicationStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                body TEXT NOT NULL,
                request_id TEXT,
                task_id TEXT,
                workflow_id TEXT,
                correlation_id TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_projection (
                task_id TEXT PRIMARY KEY,
                request_id TEXT NOT NULL,
                workflow_id TEXT,
                correlation_id TEXT NOT NULL,
                goal TEXT NOT NULL,
                status TEXT NOT NULL,
                agent_id TEXT,
                model_id TEXT,
                tools_json TEXT NOT NULL,
                resource_json TEXT NOT NULL,
                verification_json TEXT NOT NULL,
                recovery_json TEXT NOT NULL,
                error_json TEXT,
                result_json TEXT,
                attempts INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chat_conversation
                ON chat_messages(conversation_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_task_status
                ON task_projection(status, updated_at DESC);
            """
        )
        self._connection.commit()

    def ensure_conversation(self, conversation_id: str, title: str) -> None:
        now = _now().isoformat()
        with self._connection:
            self._connection.execute(
                "INSERT INTO conversations VALUES(?,?,?,?) ON CONFLICT(conversation_id) "
                "DO UPDATE SET updated_at=excluded.updated_at",
                (conversation_id, title[:160], now, now),
            )

    def add_message(
        self,
        message_id: str,
        conversation_id: str,
        role: str,
        body: str,
        *,
        request_id: str | None = None,
        task_id: str | None = None,
        workflow_id: str | None = None,
        correlation_id: str | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                "INSERT INTO chat_messages VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    message_id,
                    conversation_id,
                    role,
                    body,
                    request_id,
                    task_id,
                    workflow_id,
                    correlation_id,
                    _now().isoformat(),
                ),
            )

    def messages(self, conversation_id: str) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT * FROM chat_messages WHERE conversation_id=? ORDER BY created_at,message_id",
            (conversation_id,),
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def conversations(self) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC"
        ).fetchall()
        return tuple(dict(row) for row in rows)

    def record_task(self, item: dict[str, Any]) -> None:
        now = _now().isoformat()
        with self._connection:
            self._connection.execute(
                "INSERT INTO task_projection VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(task_id) DO UPDATE SET status=excluded.status,"
                "agent_id=excluded.agent_id,"
                "model_id=excluded.model_id,tools_json=excluded.tools_json,"
                "resource_json=excluded.resource_json,verification_json=excluded.verification_json,"
                "recovery_json=excluded.recovery_json,error_json=excluded.error_json,"
                "result_json=excluded.result_json,attempts=excluded.attempts,updated_at=excluded.updated_at",
                (
                    item["task_id"],
                    item["request_id"],
                    item.get("workflow_id"),
                    item["correlation_id"],
                    item["goal"],
                    item["status"],
                    item.get("agent_id"),
                    item.get("model_id"),
                    json.dumps(item.get("tools", [])),
                    json.dumps(item.get("resource", {}), sort_keys=True),
                    json.dumps(item.get("verification", {}), sort_keys=True),
                    json.dumps(item.get("recovery", {}), sort_keys=True),
                    None
                    if item.get("error") is None
                    else json.dumps(item["error"], sort_keys=True),
                    None if item.get("result") is None else json.dumps(item["result"], default=str),
                    int(item.get("attempts", 0)),
                    item.get("created_at", now),
                    now,
                ),
            )

    def tasks(self) -> tuple[dict[str, Any], ...]:
        rows = self._connection.execute(
            "SELECT * FROM task_projection ORDER BY updated_at DESC"
        ).fetchall()
        return tuple(self._task(row) for row in rows)

    def task(self, task_id: str) -> dict[str, Any]:
        row = self._connection.execute(
            "SELECT * FROM task_projection WHERE task_id=?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError("task not found")
        return self._task(row)

    @staticmethod
    def _task(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        for name in ("tools_json", "resource_json", "verification_json", "recovery_json"):
            item[name.removesuffix("_json")] = json.loads(item.pop(name))
        for name in ("error_json", "result_json"):
            value = item.pop(name)
            item[name.removesuffix("_json")] = None if value is None else json.loads(value)
        return item

    def close(self) -> None:
        self._connection.close()


__all__ = ["ApplicationStore"]
