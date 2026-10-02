"""Bounded Outreach/Reply/Project/QA agents composed through Agent Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from zyro.agents.definition import AgentDefinition
from zyro.agents.handler import AgentExecution, ExecutionContext
from zyro.agents.registry import AgentRegistry
from zyro.core.risk import RiskClass
from zyro.domains.freelancing.delivery import ProjectRecord, QACriterion, QAService
from zyro.domains.freelancing.outreach import OutreachPreparation
from zyro.domains.freelancing.replies import DeterministicReplyProcessor
from zyro.execution.evidence import TrustedVerificationEvidence

OUTREACH_PREPARATION_AGENT_ID = "freelancing.outreach-preparation"
REPLY_PROCESSING_AGENT_ID = "freelancing.reply-processing"
PROJECT_MANAGEMENT_AGENT_ID = "freelancing.project-management"
QA_AGENT_ID = "freelancing.qa"


class OperationalStage(StrEnum):
    OUTREACH_PREPARATION = "OUTREACH_PREPARATION"
    REPLY_PROCESSING = "REPLY_PROCESSING"
    PROJECT_MANAGEMENT = "PROJECT_MANAGEMENT"
    QA = "QA"


@dataclass(frozen=True, slots=True)
class ReplyInput:
    reply_id: str


@dataclass(frozen=True, slots=True)
class QAInput:
    project_id: str
    criteria: tuple[QACriterion, ...]
    evidence: TrustedVerificationEvidence


OperationalValue: TypeAlias = OutreachPreparation | ReplyInput | ProjectRecord | QAInput


@dataclass(frozen=True, slots=True)
class OperationalInput:
    stage: OperationalStage
    value: OperationalValue


class OperationalInputRegistry:
    def __init__(self) -> None:
        self._inputs: dict[str, OperationalInput] = {}

    def bind(self, request_id: str, value: OperationalInput) -> None:
        if request_id in self._inputs:
            raise ValueError("operational input is already bound")
        self._inputs[request_id] = value

    def get(self, request_id: str) -> OperationalInput:
        try:
            return self._inputs[request_id]
        except KeyError as error:
            raise ValueError("operational input is not bound") from error

    def discard(self, request_id: str) -> None:
        self._inputs.pop(request_id, None)


class OutreachPreparationAgent:
    """Returns an already bounded final preparation; it never executes a send."""

    def __init__(self, inputs: OperationalInputRegistry) -> None:
        self._inputs = inputs

    def execute(self, context: ExecutionContext) -> AgentExecution:
        item = self._inputs.get(context.request_id)
        if item.stage is not OperationalStage.OUTREACH_PREPARATION or not isinstance(
            item.value, OutreachPreparation
        ):
            raise ValueError("outreach preparation agent received incompatible input")
        return AgentExecution.success(item.value)


class ReplyProcessingAgent:
    def __init__(
        self,
        inputs: OperationalInputRegistry,
        processor: DeterministicReplyProcessor,
    ) -> None:
        self._inputs = inputs
        self._processor = processor

    def execute(self, context: ExecutionContext) -> AgentExecution:
        item = self._inputs.get(context.request_id)
        if item.stage is not OperationalStage.REPLY_PROCESSING or not isinstance(
            item.value, ReplyInput
        ):
            raise ValueError("reply processing agent received incompatible input")
        return AgentExecution.success(
            self._processor.process(item.value.reply_id, processing_task_id=context.task_id)
        )


class ProjectManagementAgent:
    """Produces the bounded authoritative project snapshot supplied by orchestration."""

    def __init__(self, inputs: OperationalInputRegistry) -> None:
        self._inputs = inputs

    def execute(self, context: ExecutionContext) -> AgentExecution:
        item = self._inputs.get(context.request_id)
        if item.stage is not OperationalStage.PROJECT_MANAGEMENT or not isinstance(
            item.value, ProjectRecord
        ):
            raise ValueError("project management agent received incompatible input")
        return AgentExecution.success(item.value)


class QAAgent:
    def __init__(self, inputs: OperationalInputRegistry, qa: QAService) -> None:
        self._inputs = inputs
        self._qa = qa

    def execute(self, context: ExecutionContext) -> AgentExecution:
        item = self._inputs.get(context.request_id)
        if item.stage is not OperationalStage.QA or not isinstance(item.value, QAInput):
            raise ValueError("QA agent received incompatible input")
        return AgentExecution.success(
            self._qa.evaluate(
                item.value.project_id,
                context.task_id,
                item.value.criteria,
                item.value.evidence,
            )
        )


def register_operational_agents(
    registry: AgentRegistry,
    inputs: OperationalInputRegistry,
    reply_processor: DeterministicReplyProcessor,
    qa: QAService,
) -> None:
    definitions = (
        (
            OUTREACH_PREPARATION_AGENT_ID,
            "Outreach Preparation Agent",
            "Prepare exact outbound content without sending",
            ("freelancing.outreach.prepare",),
            OutreachPreparationAgent(inputs),
        ),
        (
            REPLY_PROCESSING_AGENT_ID,
            "Reply Processing Agent",
            "Classify untrusted client reply data without executing tools",
            ("freelancing.reply.process",),
            ReplyProcessingAgent(inputs, reply_processor),
        ),
        (
            PROJECT_MANAGEMENT_AGENT_ID,
            "Project Management Agent",
            "Track bounded delivery project work",
            ("freelancing.project.manage",),
            ProjectManagementAgent(inputs),
        ),
        (
            QA_AGENT_ID,
            "QA Agent",
            "Evaluate explicit delivery criteria and evidence",
            ("freelancing.delivery.qa",),
            QAAgent(inputs, qa),
        ),
    )
    for agent_id, name, role, capabilities, handler in definitions:
        registry.register(
            AgentDefinition(
                agent_id=agent_id,
                name=name,
                version="1.0.0",
                role=role,
                domain="freelancing",
                responsibilities=(role,),
                capabilities=capabilities,
                risk_class=RiskClass.AUTOMATIC,
                input_requirements=("bounded operational input",),
                output_contract={"type": "structured freelancing operational result"},
                verification_requirements=("independent structural/domain verification",),
            ),
            handler,
        )


__all__ = [
    "OUTREACH_PREPARATION_AGENT_ID",
    "PROJECT_MANAGEMENT_AGENT_ID",
    "QA_AGENT_ID",
    "REPLY_PROCESSING_AGENT_ID",
    "OperationalInput",
    "OperationalInputRegistry",
    "OperationalStage",
    "QAInput",
    "ReplyInput",
    "register_operational_agents",
]
