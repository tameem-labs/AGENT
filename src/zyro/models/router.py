"""Deterministic provider-independent model selection and invocation boundary."""

from __future__ import annotations

from math import inf

from zyro.core.errors import ErrorInfo
from zyro.core.logging import LogContext, get_logger
from zyro.models.contracts import (
    ModelDefinition,
    ModelDeployment,
    ModelRequest,
    ModelRequirements,
    ModelResult,
    ModelResultStatus,
    PrivacyRequirement,
    RoutingDecision,
    RoutingPreference,
    RoutingStatus,
)
from zyro.models.errors import MissingProviderError
from zyro.models.provider import ModelProvider, ProviderRegistry
from zyro.models.registry import ModelRegistry


def build_model_router(
    models: tuple[ModelDefinition, ...], providers: tuple[ModelProvider, ...]
) -> ModelRouter:
    """Composition helper retaining provider-registry ownership inside the router module."""
    model_registry = ModelRegistry()
    for model in models:
        model_registry.register(model)
    provider_registry = ProviderRegistry()
    for provider in providers:
        provider_registry.register(provider)
    return ModelRouter(model_registry, provider_registry)


class ModelRouter:
    """Own model/provider selection so agents request capabilities, not vendors."""

    def __init__(self, models: ModelRegistry, providers: ProviderRegistry) -> None:
        self._models = models
        self._providers = providers

    def route(self, requirements: ModelRequirements) -> RoutingDecision:
        compatible = [
            model for model in self._models.list() if self._is_compatible(model, requirements)
        ]
        available: list[ModelDefinition] = []
        for model in compatible:
            try:
                provider = self._providers.get(model.provider_id)
            except MissingProviderError:
                continue
            if provider.available or not requirements.availability_required:
                available.append(model)

        if available:
            selected = min(available, key=lambda model: self._rank(model, requirements))
            return RoutingDecision(RoutingStatus.SELECTED, model=selected)
        if compatible:
            error = ErrorInfo(
                code="model_provider_unavailable",
                message="Compatible models exist, but no registered provider is available.",
                error_type="ProviderUnavailable",
                retryable=True,
            )
            return RoutingDecision(RoutingStatus.PROVIDER_UNAVAILABLE, error=error)
        error = ErrorInfo(
            code="no_eligible_model",
            message="No registered model satisfies the requested capabilities.",
            error_type="RoutingFailure",
        )
        return RoutingDecision(RoutingStatus.NO_ELIGIBLE_MODEL, error=error)

    def invoke(self, request: ModelRequest) -> ModelResult:
        """Validate, route, and invoke one provider without exposing it to the agent."""
        validation_error = request.validation_error()
        if validation_error is not None:
            return self._failure(
                request,
                ModelResultStatus.INVALID_REQUEST,
                ErrorInfo(
                    code="invalid_model_request",
                    message=validation_error,
                    error_type="InvalidModelRequest",
                ),
            )

        decision = self.route(request.requirements)
        if decision.status is not RoutingStatus.SELECTED:
            assert decision.error is not None
            status = (
                ModelResultStatus.PROVIDER_UNAVAILABLE
                if decision.status is RoutingStatus.PROVIDER_UNAVAILABLE
                else ModelResultStatus.NO_ELIGIBLE_MODEL
            )
            return self._failure(request, status, decision.error)

        assert decision.model is not None
        model = decision.model
        try:
            provider = self._providers.get(model.provider_id)
        except MissingProviderError:
            return self._failure(
                request,
                ModelResultStatus.PROVIDER_UNAVAILABLE,
                ErrorInfo(
                    code="model_provider_unavailable",
                    message="The selected model provider is not registered.",
                    error_type="ProviderUnavailable",
                    retryable=True,
                ),
                model,
            )
        if not provider.available:
            return self._failure(
                request,
                ModelResultStatus.PROVIDER_UNAVAILABLE,
                ErrorInfo(
                    code="model_provider_unavailable",
                    message="The selected model provider is unavailable.",
                    error_type="ProviderUnavailable",
                    retryable=True,
                ),
                model,
            )

        logger = get_logger(
            "model_router",
            LogContext(
                request_id=request.request_id,
                task_id=request.task_id,
                agent_id=request.agent_id,
                instance_id=request.instance_id,
                correlation_id=request.correlation_id,
            ),
        )
        logger.info("model invocation started")
        try:
            result = provider.invoke(model, request)
        except TimeoutError:
            logger.warning("model invocation timed out")
            return self._failure(
                request,
                ModelResultStatus.TIMEOUT,
                ErrorInfo(
                    code="model_invocation_timeout",
                    message="The model provider timed out.",
                    error_type="TimeoutError",
                    retryable=True,
                ),
                model,
            )
        except Exception as error:
            logger.error("model provider raised an exception")
            return self._failure(
                request,
                ModelResultStatus.INVOCATION_EXCEPTION,
                ErrorInfo(
                    code="model_invocation_exception",
                    message=f"The model provider raised {type(error).__name__}.",
                    error_type=type(error).__name__,
                    retryable=True,
                ),
                model,
            )

        if not isinstance(result, ModelResult) or not self._matches_request(result, request, model):
            logger.error("model provider returned a malformed response")
            return self._failure(
                request,
                ModelResultStatus.MALFORMED_RESPONSE,
                ErrorInfo(
                    code="malformed_model_response",
                    message="The model provider returned an invalid response contract.",
                    error_type="MalformedProviderResponse",
                ),
                model,
            )
        if result.succeeded:
            logger.info("model invocation succeeded")
        else:
            logger.warning("model invocation failed")
        return result

    def _is_compatible(
        self,
        model: ModelDefinition,
        requirements: ModelRequirements,
    ) -> bool:
        if not model.enabled:
            return False
        if not requirements.required_capabilities.issubset(model.capabilities):
            return False
        if not requirements.required_modalities.issubset(model.modalities):
            return False
        if model.max_context_tokens < requirements.minimum_context_tokens:
            return False
        if model.max_complexity < requirements.complexity:
            return False
        if requirements.tool_calling_required and not model.supports_tool_calling:
            return False
        if requirements.structured_output_required and not model.supports_structured_output:
            return False
        if requirements.maximum_cost_per_million is not None:
            cost = model.estimated_total_cost
            if cost is None or cost > requirements.maximum_cost_per_million:
                return False
        if requirements.maximum_latency_ms is not None:
            latency = model.typical_latency_ms
            if latency is None or latency > requirements.maximum_latency_ms:
                return False
        if (
            requirements.privacy is PrivacyRequirement.PRIVATE_OR_LOCAL
            and model.deployment is ModelDeployment.CLOUD
        ):
            return False
        return not (
            requirements.privacy is PrivacyRequirement.LOCAL_ONLY
            and model.deployment is not ModelDeployment.LOCAL
        )

    @staticmethod
    def _rank(
        model: ModelDefinition,
        requirements: ModelRequirements,
    ) -> tuple[float, float, int, str]:
        cost_rank = 0.0
        if requirements.cost_preference is RoutingPreference.LOWEST:
            cost = model.estimated_total_cost
            cost_rank = inf if cost is None else cost
        latency_rank = 0.0
        if requirements.latency_preference is RoutingPreference.LOWEST:
            latency = model.typical_latency_ms
            latency_rank = inf if latency is None else float(latency)
        complexity_excess = int(model.max_complexity - requirements.complexity)
        return (cost_rank, latency_rank, complexity_excess, model.model_id)

    @staticmethod
    def _matches_request(
        result: ModelResult,
        request: ModelRequest,
        model: ModelDefinition,
    ) -> bool:
        return (
            result.request_id == request.request_id
            and result.task_id == request.task_id
            and result.agent_id == request.agent_id
            and result.instance_id == request.instance_id
            and result.correlation_id == request.correlation_id
            and result.provider_id == model.provider_id
            and result.model_id == model.model_id
        )

    @staticmethod
    def _failure(
        request: ModelRequest,
        status: ModelResultStatus,
        error: ErrorInfo,
        model: ModelDefinition | None = None,
    ) -> ModelResult:
        return ModelResult(
            status=status,
            request_id=request.request_id,
            task_id=request.task_id,
            agent_id=request.agent_id,
            instance_id=request.instance_id,
            correlation_id=request.correlation_id,
            provider_id=None if model is None else model.provider_id,
            model_id=None if model is None else model.model_id,
            error=error,
        )
