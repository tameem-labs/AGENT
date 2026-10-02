"""Translate safe application configuration into Resource policy."""

from zyro.core.config import AppConfig
from zyro.resources.contracts import ResourcePolicy


def resource_policy_from_config(config: AppConfig) -> ResourcePolicy:
    return ResourcePolicy(
        task_token_limit=config.task_token_limit,
        workflow_token_limit=config.workflow_token_limit,
        max_concurrent_tasks=config.max_concurrent_tasks,
        max_concurrent_agents=config.max_concurrent_agents,
        max_concurrent_tool_calls=config.max_concurrent_tool_calls,
    )


__all__ = ["resource_policy_from_config"]
