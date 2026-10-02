from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import count

from zyro.core.scope import ResourceScope
from zyro.security.permission import (
    Permission,
    PermissionEvaluator,
    PermissionScope,
    PermissionStore,
    PrincipalDirectory,
)
from zyro.security.resource_authorization import PermissionResourceAuthorizer

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


@dataclass
class MutableClock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, *, days: int = 0, seconds: int = 0) -> None:
        self.now += timedelta(days=days, seconds=seconds)


def resource_authorizer(
    scope: ResourceScope,
    *,
    principals: Iterable[str] = ("owner-1",),
    restricted: bool = False,
) -> PermissionResourceAuthorizer:
    capabilities = {
        "memory.write",
        "memory.read",
        "state.read",
        "knowledge.write",
        "knowledge.read",
        "context.assemble",
    }
    if restricted:
        capabilities.add("memory.read.restricted")
    sequence = count(1)
    store = PermissionStore()
    principal_tuple = tuple(principals)
    for principal in principal_tuple:
        for capability in sorted(capabilities):
            store.add(
                Permission(
                    f"permission-{next(sequence)}",
                    principal,
                    capability,
                    PermissionScope(target=scope.target),
                    "resources-v1",
                    issued_at=NOW - timedelta(minutes=1),
                )
            )
    evaluator = PermissionEvaluator(
        store,
        PrincipalDirectory(principal_tuple),
        frozenset(capabilities),
        policy_version="resources-v1",
        clock=lambda: NOW,
        id_factory=lambda: f"decision-{next(sequence)}",
    )
    return PermissionResourceAuthorizer(evaluator)
