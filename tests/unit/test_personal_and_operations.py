"""Unit tests for Personal and System/Operations Department tools and agents."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from zyro.agents.registry import AgentRegistry
from zyro.domains.operations import (
    DatabaseCheckHandler,
    SystemHealthCheckHandler,
    register_operations_agents,
)
from zyro.domains.personal import (
    PersonalAgendaHandler,
    PersonalReminderHandler,
    register_personal_agents,
)
from zyro.tools.contracts import ToolExecutionContext


def _ctx() -> ToolExecutionContext:
    return ToolExecutionContext("tool.ops", "req-1", "task-1", "agent-1", "inst-1", "corr-1")


def test_personal_agenda() -> None:
    handler = PersonalAgendaHandler()
    res = handler.execute(_ctx(), {"date": "2026-10-02"})
    assert res.succeeded
    assert res.output is not None
    assert res.output["date"] == "2026-10-02"
    assert len(res.output["items"]) > 0
    assert len(res.output["focus_blocks"]) > 0


def test_personal_reminder() -> None:
    handler = PersonalReminderHandler()

    # Missing title
    err = handler.execute(_ctx(), {"title": ""})
    assert not err.succeeded
    assert err.error is not None
    assert err.error.code == "missing_title"

    # Valid reminder
    res = handler.execute(_ctx(), {"title": "Verify backup catalog", "hours_from_now": 3})
    assert res.succeeded
    assert res.output is not None
    assert res.output["title"] == "Verify backup catalog"
    assert "reminder_id" in res.output
    assert "due_at" in res.output


def test_operations_database_check_and_health(tmp_path: Path) -> None:
    # Create a healthy sqlite database
    db_file = tmp_path / "valid.sqlite"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, name TEXT)")
    conn.execute("INSERT INTO test VALUES (1, 'zyro')")
    conn.commit()
    conn.close()

    db_handler = DatabaseCheckHandler(tmp_path)
    res = db_handler.execute(_ctx(), {})
    assert res.succeeded
    assert res.output is not None
    assert res.output["all_healthy"] is True
    assert len(res.output["databases"]) == 1
    assert res.output["databases"][0]["status"] == "HEALTHY"

    # System Health check
    sys_handler = SystemHealthCheckHandler(tmp_path)
    h_res = sys_handler.execute(_ctx(), {})
    assert h_res.succeeded
    assert h_res.output is not None
    assert h_res.output["host_status"] == "HEALTHY"


def test_register_personal_and_operations_agents() -> None:
    reg = AgentRegistry()
    register_personal_agents(reg)
    assert reg.get("personal.organizer") is not None
    assert reg.get("personal.reminders") is not None

    register_operations_agents(reg)
    assert reg.get("operations.health") is not None
    assert reg.get("operations.integrity") is not None
