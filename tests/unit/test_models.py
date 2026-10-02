from dataclasses import dataclass

import pytest

from tests.fakes import DeterministicModelProvider
from zyro.core.errors import ErrorInfo
from zyro.models.contracts import (
    ModelComplexity,
    ModelDefinition,
    ModelDeployment,
    ModelRequest,
    ModelRequirements,
    ModelResult,
    ModelResultStatus,
    PrivacyRequirement,
    RoutingPreference,
    RoutingStatus,
)
from zyro.models.errors import (
    DuplicateModelError,
    DuplicateProviderError,
    InvalidModelDefinitionError,
    InvalidModelRequirementsError,
    MissingModelError,
    MissingProviderError,
)
from zyro.models.provider import ProviderRegistry
from zyro.models.registry import ModelRegistry
from zyro.models.router import ModelRouter


def model(
    model_id: str = "model-a",
    *,
    provider_id: str = "test-provider",
    capabilities: frozenset[str] = frozenset({"reasoning"}),
    tool_calling: bool = False,
    deployment: ModelDeployment = ModelDeployment.CLOUD,
    cost: float | None = 1.0,
    latency: int | None = 100,
    enabled: bool = True,
) -> ModelDefinition:
    return ModelDefinition(
        model_id=model_id,
        provider_id=provider_id,
        name=model_id,
        capabilities=capabilities,
        modalities=frozenset({"text"}),
        max_context_tokens=8_192,
        max_complexity=ModelComplexity.COMPLEX,
        supports_tool_calling=tool_calling,
        supports_structured_output=True,
        deployment=deployment,
        input_cost_per_million=cost,
        output_cost_per_million=cost,
        typical_latency_ms=latency,
        enabled=enabled,
        configuration_reference="providers.test.models.default",
    )


def requirements(**overrides: object) -> ModelRequirements:
    values: dict[str, object] = {
        "task_type": "reasoning",
        "required_capabilities": frozenset({"reasoning"}),
        "required_modalities": frozenset({"text"}),
        "minimum_context_tokens": 4_096,
        "complexity": ModelComplexity.MEDIUM,
    }
    values.update(overrides)
    return ModelRequirements(**values)  # type: ignore[arg-type]


def request(requirement: ModelRequirements | None = None, prompt: str = "solve") -> ModelRequest:
    return ModelRequest(
        prompt=prompt,
        requirements=requirement or requirements(),
        request_id="request-1",
        task_id="task-1",
        agent_id="agent-1",
        instance_id="instance-1",
        correlation_id="correlation-1",
    )


def router_with(
    provider: DeterministicModelProvider,
    *definitions: ModelDefinition,
) -> ModelRouter:
    providers = ProviderRegistry()
    providers.register(provider)
    models = ModelRegistry()
    for definition in definitions:
        models.register(definition)
    return ModelRouter(models, providers)


def test_model_definition_and_requirements_validation() -> None:
    with pytest.raises(InvalidModelDefinitionError, match="max_context_tokens"):
        model("invalid").__class__(
            model_id="invalid",
            provider_id="test-provider",
            name="Invalid",
            capabilities=frozenset({"reasoning"}),
            modalities=frozenset({"text"}),
            max_context_tokens=0,
        )
    with pytest.raises(InvalidModelRequirementsError, match="minimum_context_tokens"):
        requirements(minimum_context_tokens=0)
    with pytest.raises(InvalidModelDefinitionError, match="secret fields"):
        ModelDefinition(
            model_id="unsafe",
            provider_id="test-provider",
            name="Unsafe",
            capabilities=frozenset({"reasoning"}),
            modalities=frozenset({"text"}),
            max_context_tokens=100,
            metadata={"api_key": "redacted"},
        )


def test_provider_and_model_registration_are_stable_and_reject_duplicates() -> None:
    provider = DeterministicModelProvider()
    providers = ProviderRegistry()
    providers.register(provider)
    with pytest.raises(DuplicateProviderError):
        providers.register(provider)

    models = ModelRegistry()
    models.register(model("model-b"))
    models.register(model("model-a"))
    with pytest.raises(DuplicateModelError):
        models.register(model("model-a"))

    assert providers.get("test-provider") is provider
    assert [item.model_id for item in models.list()] == ["model-a", "model-b"]
    with pytest.raises(MissingProviderError):
        providers.get("missing")
    with pytest.raises(MissingModelError):
        models.get("missing")


def test_router_selects_compatible_model_deterministically() -> None:
    provider = DeterministicModelProvider()
    router = router_with(provider, model("model-z"), model("model-a"))

    decisions = [router.route(requirements()) for _ in range(3)]

    assert all(decision.status is RoutingStatus.SELECTED for decision in decisions)
    assert [decision.model.model_id for decision in decisions if decision.model] == [
        "model-a",
        "model-a",
        "model-a",
    ]


def test_router_applies_capability_tool_privacy_cost_and_latency_requirements() -> None:
    provider = DeterministicModelProvider()
    router = router_with(
        provider,
        model("cloud", tool_calling=False, cost=0.1, latency=10),
        model(
            "local-capable",
            tool_calling=True,
            deployment=ModelDeployment.LOCAL,
            cost=0.2,
            latency=20,
        ),
    )
    decision = router.route(
        requirements(
            tool_calling_required=True,
            privacy=PrivacyRequirement.LOCAL_ONLY,
            maximum_cost_per_million=1.0,
            maximum_latency_ms=30,
        )
    )

    assert decision.model is not None
    assert decision.model.model_id == "local-capable"


def test_lowest_cost_preference_changes_deterministic_selection() -> None:
    provider = DeterministicModelProvider()
    router = router_with(
        provider,
        model("expensive", cost=5.0),
        model("cheap", cost=0.1),
    )

    decision = router.route(requirements(cost_preference=RoutingPreference.LOWEST))

    assert decision.model is not None
    assert decision.model.model_id == "cheap"


def test_router_returns_structured_no_eligible_model() -> None:
    decision = router_with(
        DeterministicModelProvider(),
        model("text-only", capabilities=frozenset({"summarization"})),
    ).route(requirements())

    assert decision.status is RoutingStatus.NO_ELIGIBLE_MODEL
    assert decision.error is not None
    assert decision.error.code == "no_eligible_model"


def test_router_distinguishes_provider_unavailability() -> None:
    provider = DeterministicModelProvider(available=False)
    result = router_with(provider, model()).invoke(request())

    assert result.status is ModelResultStatus.PROVIDER_UNAVAILABLE
    assert result.error is not None
    assert result.error.retryable
    assert provider.requests == []


def test_provider_invocation_success_preserves_correlation() -> None:
    provider = DeterministicModelProvider(content="answer")
    result = router_with(provider, model()).invoke(request())

    assert result.succeeded
    assert result.content == "answer"
    assert result.provider_id == "test-provider"
    assert result.model_id == "model-a"
    assert result.correlation_id == "correlation-1"
    assert provider.requests[0].agent_id == "agent-1"


def test_provider_structured_failure_is_not_success() -> None:
    provider = DeterministicModelProvider(
        failure=ErrorInfo("provider_failed", "Provider failed.", "ProviderFailure", True)
    )
    result = router_with(provider, model()).invoke(request())

    assert result.status is ModelResultStatus.PROVIDER_FAILURE
    assert not result.succeeded
    assert result.error is provider.failure


@dataclass
class TimeoutProvider(DeterministicModelProvider):
    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        raise TimeoutError("details must not escape")


@dataclass
class RaisingProvider(DeterministicModelProvider):
    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        raise RuntimeError("sensitive provider details")


@dataclass
class MalformedProvider(DeterministicModelProvider):
    def invoke(self, model: ModelDefinition, request: ModelRequest) -> ModelResult:
        return object()  # type: ignore[return-value]


def test_timeout_and_provider_exception_are_structured_and_sanitized() -> None:
    timeout = router_with(TimeoutProvider(), model()).invoke(request())
    raised = router_with(RaisingProvider(), model()).invoke(request())

    assert timeout.status is ModelResultStatus.TIMEOUT
    assert timeout.error is not None and timeout.error.retryable
    assert raised.status is ModelResultStatus.INVOCATION_EXCEPTION
    assert raised.error is not None
    assert "sensitive provider details" not in raised.error.message


def test_malformed_provider_response_is_rejected() -> None:
    result = router_with(MalformedProvider(), model()).invoke(request())

    assert result.status is ModelResultStatus.MALFORMED_RESPONSE
    assert result.error is not None
    assert result.error.code == "malformed_model_response"


def test_invalid_model_request_returns_structured_failure() -> None:
    result = router_with(DeterministicModelProvider(), model()).invoke(request(prompt=" "))

    assert result.status is ModelResultStatus.INVALID_REQUEST
    assert result.error is not None
    assert result.error.code == "invalid_model_request"
