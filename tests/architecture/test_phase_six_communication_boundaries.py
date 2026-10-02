from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMUNICATION = ROOT / "src" / "zyro" / "communication"


def imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            found.add(node.module)
    return found


def test_communication_reuses_security_and_has_no_external_broker() -> None:
    combined: set[str] = set()
    for path in COMMUNICATION.glob("*.py"):
        combined.update(imports(path))

    assert "zyro.security.permission" in imports(COMMUNICATION / "authorization.py")
    forbidden = {
        "kafka",
        "pika",
        "redis",
        "celery",
        "requests",
        "threading",
        "asyncio",
        "zyro.memory",
        "zyro.knowledge",
    }
    assert forbidden.isdisjoint(combined)


def test_bus_does_not_own_task_or_freelancing_state() -> None:
    bus_source = (COMMUNICATION / "event_bus.py").read_text()
    persistence_source = (COMMUNICATION / "persistence.py").read_text()
    bus_imports = imports(COMMUNICATION / "event_bus.py")

    assert "zyro.core.task" not in bus_imports
    assert "zyro.domains.freelancing" not in bus_imports
    assert "LeadState" not in bus_source
    assert "TaskStatus" not in bus_source
    assert "lead_state" not in persistence_source
    assert "task_state" not in persistence_source


def test_direct_message_and_event_are_distinct_locked_envelopes() -> None:
    contracts_tree = ast.parse((COMMUNICATION / "contracts.py").read_text())
    events_tree = ast.parse((ROOT / "src" / "zyro" / "core" / "events.py").read_text())
    contract_names = {
        node.name for node in ast.walk(contracts_tree) if isinstance(node, ast.ClassDef)
    }
    event_names = {node.name for node in ast.walk(events_tree) if isinstance(node, ast.ClassDef)}

    assert "DirectMessage" in contract_names
    assert "Event" not in contract_names
    assert "Event" in event_names
    assert "DirectMessage" not in event_names


def test_persistence_metadata_stays_out_of_canonical_envelopes() -> None:
    contracts_source = (COMMUNICATION / "contracts.py").read_text()
    event_source = (ROOT / "src" / "zyro" / "core" / "events.py").read_text()

    direct_section = contracts_source.split("class DirectMessage:", 1)[1].split(
        "class MessageDeliveryPolicy", 1
    )[0]
    event_section = event_source.split("class Event:", 1)[1].split("class EventPublisher", 1)[0]
    for forbidden_field in ("delivery_id:", "attempt_id:", "subscriber_id:"):
        assert forbidden_field not in direct_section
        assert forbidden_field not in event_section
