"""Agent handlers for the Operations Department."""

from __future__ import annotations

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.risk import RiskClass


class HealthMonitorAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool("operations.health_check", {})
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        summary = (
            f"System Health: {output.get('host_status')}. "
            f"Checked {len(output.get('databases', []))} database stores."
        )
        return AgentExecution.success(
            {"message": summary, "health": dict(output)},
            tool_results=(tool_res,),
        )


class DatabaseIntegrityAgentHandler:
    def execute(self, context: ExecutionContext) -> AgentExecution:
        tool_res = context.execute_tool("operations.database_check", {})
        if not tool_res.succeeded:
            assert tool_res.error is not None
            return AgentExecution.failure(tool_res.error, tool_results=(tool_res,))
        output = tool_res.output or {}
        all_ok = output.get("all_healthy", False)
        if all_ok:
            status_str = "All databases verified healthy"
        else:
            status_str = "Database integrity issues detected"
        return AgentExecution.success(
            {"message": status_str, "integrity": dict(output)},
            tool_results=(tool_res,),
        )


def register_operations_agents(registry: AgentRegistry) -> None:
    health_def = AgentDefinition(
        "operations.health",
        "System Health Monitor Agent",
        "1.0.0",
        "Monitor system resources, host health, and service responsiveness",
        "operations",
        ("health monitoring", "telemetry"),
        ("operations.health_check",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(health_def, HealthMonitorAgentHandler())

    integrity_def = AgentDefinition(
        "operations.integrity",
        "Database Integrity Agent",
        "1.0.0",
        "Verify WAL journal state and PRAGMA quick_check integrity across all stores",
        "operations",
        ("database check", "integrity verification"),
        ("operations.database_check",),
        risk_class=RiskClass.AUTOMATIC,
    )
    registry.register(integrity_def, DatabaseIntegrityAgentHandler())


__all__ = [
    "DatabaseIntegrityAgentHandler",
    "HealthMonitorAgentHandler",
    "register_operations_agents",
]
