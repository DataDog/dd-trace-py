from __future__ import annotations

from contextvars import ContextVar
from contextvars import Token
from dataclasses import dataclass
import math
from numbers import Integral
import re
import time
from typing import Any
from typing import Callable
from typing import Mapping
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.metrics import DogStatsdClient
from ddtrace.internal.metrics import MetricsClient


log = get_logger(__name__)


PROVIDER_ATTEMPT_PROFILE = "gen_ai.client.provider_attempt@0.1.0"
PROVIDER_STREAMING_PROFILE = "gen_ai.client.provider_streaming@0.1.0"
GATEWAY_REQUEST_PROFILE = "trajectory.gen_ai.gateway.request@0.1.0"
PORTABLE_METRIC_SUITE_REVISION = "e841dbc078107e45c95f0b0c02346039f7cd2677"

_OPERATION_NAMES = {
    "completion": "chat",
    "acompletion": "chat",
    "text_completion": "text_completion",
    "atext_completion": "text_completion",
}

_PROVIDER_NAMES = {
    "anthropic": "anthropic",
    "azure": "azure.ai.openai",
    "azure_ai": "azure.ai.inference",
    "azure_text": "azure.ai.openai",
    "bedrock": "aws.bedrock",
    "cohere": "cohere",
    "cohere_chat": "cohere",
    "deepseek": "deepseek",
    "gemini": "gcp.gemini",
    "groq": "groq",
    "mistral": "mistral_ai",
    "mistral_ai": "mistral_ai",
    "openai": "openai",
    "perplexity": "perplexity",
    "vertex_ai": "gcp.vertex_ai",
    "vertex_ai_beta": "gcp.vertex_ai",
    "watsonx": "ibm.watsonx.ai",
    "xai": "x_ai",
}

_LOW_CARDINALITY_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")


def _get_value(value: Any, key: str, default: Any = None) -> Any:
    try:
        if isinstance(value, dict):
            return value.get(key, default)
        return getattr(value, key, default)
    except Exception:
        return default


def _non_negative_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        normalized = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(normalized) or normalized < 0:
        return None
    return normalized


def _non_negative_integer(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
        return None
    return int(value)


def _reported_non_negative_integer(value: Any) -> Optional[int]:
    normalized = _non_negative_integer(value)
    if normalized is not None:
        return normalized
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    return None


def _get_reported_value(value: Any, *keys: str) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value:
                return value[key]
        return None

    fields_set = _get_value(value, "model_fields_set")
    if fields_set is None:
        fields_set = _get_value(value, "__fields_set__")
    if fields_set is not None:
        for key in keys:
            if key in fields_set:
                return _get_value(value, key)
        return None

    for key in keys:
        reported = _get_value(value, key)
        if reported is not None:
            return reported
    return None


def _normalize_provider(provider: Any) -> Optional[str]:
    if not isinstance(provider, str):
        return None
    provider = provider.strip().lower()
    if not provider or provider == "unknown":
        return None
    provider = _PROVIDER_NAMES.get(provider, provider)
    provider = re.sub(r"[^a-z0-9_.-]+", "_", provider)
    if not _LOW_CARDINALITY_IDENTIFIER.fullmatch(provider):
        return None
    return provider


def _normalize_error_type(exception: BaseException) -> str:
    name = exception.__class__.__name__
    name = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    name = re.sub(r"[^a-z0-9_.-]+", "_", name).strip("_")
    return "litellm.{}".format(name or "error")


@dataclass
class LiteLLMProviderAttempt:
    operation_name: str
    requested_model: Optional[str]
    started_at: float
    streaming: bool
    first_chunk_at: Optional[float] = None
    previous_chunk_at: Optional[float] = None
    output_chunk_intervals: Optional[list[float]] = None
    response_model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None
    cost_response: Any = None
    cache_hit: bool = False
    gateway_request: Optional["LiteLLMGatewayRequest"] = None
    finalized: bool = False


@dataclass
class LiteLLMGatewayRequest:
    operation_name: str
    requested_model: Optional[str]
    started_at: float
    streaming: bool
    fallbacks_configured: bool
    provider_operations: int = 0
    attempted_retries: Optional[int] = None
    attempted_fallbacks: Optional[int] = None
    cache_outcome: Optional[str] = None
    context_token: Optional[Token[Optional[LiteLLMGatewayRequest]]] = None
    finalized: bool = False


class LiteLLMProviderMetrics:
    """Emit Open Trajectory LiteLLM metric profiles independently of trace export."""

    def __init__(
        self,
        enabled: bool,
        client: Optional[MetricsClient] = None,
        clock: Callable[[], float] = time.monotonic,
        cost_calculator: Optional[Callable[..., float]] = None,
    ) -> None:
        self.enabled = enabled
        self._client = client
        self._clock = clock
        self._cost_calculator = cost_calculator
        self._current_gateway_request: ContextVar[Optional[LiteLLMGatewayRequest]] = ContextVar(
            "ddtrace_litellm_gateway_request",
            default=None,
        )

    @property
    def client(self) -> MetricsClient:
        if self._client is None:
            self._client = DogStatsdClient()
        return self._client

    def start_attempt(
        self,
        operation: str,
        requested_model: Optional[str],
        streaming: bool,
    ) -> Optional[LiteLLMProviderAttempt]:
        if not self.enabled:
            return None
        operation_name = _OPERATION_NAMES.get(operation)
        if operation_name is None:
            return None
        return LiteLLMProviderAttempt(
            operation_name=operation_name,
            requested_model=requested_model,
            started_at=self._clock(),
            streaming=streaming,
            output_chunk_intervals=[],
            gateway_request=self._current_gateway_request.get(),
        )

    def observe_chunk(self, attempt: Optional[LiteLLMProviderAttempt], response: Any) -> None:
        if attempt is None or attempt.finalized:
            return
        observed_at = self._clock()
        if attempt.first_chunk_at is None:
            attempt.first_chunk_at = observed_at
        elif attempt.previous_chunk_at is not None and attempt.output_chunk_intervals is not None:
            attempt.output_chunk_intervals.append(max(0.0, observed_at - attempt.previous_chunk_at))
        attempt.previous_chunk_at = observed_at
        self.observe_response(attempt, response)

    def observe_first_chunk(self, attempt: Optional[LiteLLMProviderAttempt], response: Any) -> None:
        """Backward-compatible alias retained for the existing stream handler contract."""
        self.observe_chunk(attempt, response)

    def observe_response(self, attempt: Optional[LiteLLMProviderAttempt], response: Any) -> None:
        if attempt is None or attempt.finalized or response is None:
            return
        if isinstance(response, list):
            for item in response:
                self.observe_response(attempt, item)
            return

        hidden_params = _get_value(response, "_hidden_params", {})
        if _get_value(hidden_params, "cache_hit") is True:
            attempt.cache_hit = True

        response_model = _get_value(response, "model")
        if isinstance(response_model, str) and response_model:
            attempt.response_model = response_model

        usage = _get_value(response, "usage")
        if usage is not None:
            input_tokens = _non_negative_integer(_get_reported_value(usage, "prompt_tokens", "input_tokens"))
            output_tokens = _non_negative_integer(_get_reported_value(usage, "completion_tokens", "output_tokens"))
            if input_tokens is not None:
                attempt.input_tokens = input_tokens
            if output_tokens is not None:
                attempt.output_tokens = output_tokens

            reported_cost = _non_negative_number(_get_reported_value(usage, "cost"))
            if reported_cost is not None:
                attempt.cost_usd = reported_cost
                attempt.cost_source = "provider_reported"

            if input_tokens is not None or output_tokens is not None or reported_cost is not None:
                attempt.cost_response = response

        if attempt.cost_source != "provider_reported":
            calculated_cost = _non_negative_number(_get_value(hidden_params, "response_cost"))
            if calculated_cost is None:
                calculated_cost = _non_negative_number(_get_value(response, "response_cost"))
            if calculated_cost is not None:
                attempt.cost_usd = calculated_cost
                attempt.cost_source = "gateway_calculated"

    def finish_attempt(
        self,
        attempt: Optional[LiteLLMProviderAttempt],
        model_map: Mapping[str, tuple[str, str]],
        response: Any = None,
        exception: Optional[BaseException] = None,
    ) -> None:
        if attempt is None or attempt.finalized:
            return
        finished_at = self._clock()
        self.observe_response(attempt, response)
        attempt.finalized = True

        if attempt.gateway_request is not None and not attempt.cache_hit:
            attempt.gateway_request.provider_operations += 1

        # A LiteLLM cache hit returns through the public completion function but
        # does not issue a provider operation. Counting it as a provider attempt
        # would overstate latency, usage, cost, and attempt cardinality.
        if attempt.cache_hit:
            return

        request_model, provider = self._resolve_model_and_provider(attempt.requested_model, model_map)
        provider_name = _normalize_provider(provider)
        if provider_name is None:
            return

        if attempt.cost_usd is None and attempt.cost_response is not None and self._cost_calculator is not None:
            try:
                calculated_cost = _non_negative_number(self._cost_calculator(completion_response=attempt.cost_response))
            except Exception:
                log.debug("Failed to calculate LiteLLM provider-attempt cost", exc_info=True)
            else:
                if calculated_cost is not None:
                    attempt.cost_usd = calculated_cost
                    attempt.cost_source = "gateway_calculated"

        base_tags = {
            "gen_ai.operation.name": attempt.operation_name,
            "gen_ai.provider.name": provider_name,
        }
        if request_model:
            base_tags["gen_ai.request.model"] = request_model
        if attempt.response_model:
            base_tags["gen_ai.response.model"] = attempt.response_model

        duration_tags = dict(base_tags)
        if exception is not None:
            duration_tags["error.type"] = _normalize_error_type(exception)
        self._distribution(
            "gen_ai.client.operation.duration",
            max(0.0, finished_at - attempt.started_at),
            duration_tags,
        )

        if attempt.input_tokens is not None:
            self._distribution(
                "gen_ai.client.token.usage",
                attempt.input_tokens,
                {**base_tags, "gen_ai.token.type": "input"},
            )
        if attempt.output_tokens is not None:
            self._distribution(
                "gen_ai.client.token.usage",
                attempt.output_tokens,
                {**base_tags, "gen_ai.token.type": "output"},
            )
        if attempt.streaming and attempt.first_chunk_at is not None:
            self._distribution(
                "gen_ai.client.operation.time_to_first_chunk",
                max(0.0, attempt.first_chunk_at - attempt.started_at),
                base_tags,
            )
        for interval in attempt.output_chunk_intervals or ():
            self._distribution(
                "gen_ai.client.operation.time_per_output_chunk",
                interval,
                base_tags,
            )
        if attempt.cost_usd is not None and attempt.cost_source is not None:
            self._distribution(
                "trajectory.gen_ai.client.operation.cost",
                attempt.cost_usd,
                {**base_tags, "trajectory.cost.source": attempt.cost_source},
            )

    def start_gateway_request(
        self,
        operation: str,
        requested_model: Optional[str],
        streaming: bool,
        fallbacks_configured: bool = False,
    ) -> Optional[LiteLLMGatewayRequest]:
        if not self.enabled:
            return None
        operation_name = _OPERATION_NAMES.get(operation.removeprefix("router."))
        if operation_name is None:
            return None
        request = LiteLLMGatewayRequest(
            operation_name=operation_name,
            requested_model=requested_model,
            started_at=self._clock(),
            streaming=streaming,
            fallbacks_configured=fallbacks_configured,
        )
        request.context_token = self._current_gateway_request.set(request)
        return request

    def detach_gateway_request(self, request: Optional[LiteLLMGatewayRequest]) -> None:
        if request is None or request.context_token is None:
            return
        try:
            self._current_gateway_request.reset(request.context_token)
        except (RuntimeError, ValueError):
            log.debug("Failed to detach LiteLLM gateway metric context", exc_info=True)
        request.context_token = None

    def observe_gateway_response(self, request: Optional[LiteLLMGatewayRequest], response: Any) -> None:
        if request is None or request.finalized or response is None:
            return
        if isinstance(response, list):
            for item in response:
                self.observe_gateway_response(request, item)
            return

        hidden_params = _get_value(response, "_hidden_params", {})
        if _get_value(hidden_params, "cache_hit") is True:
            request.cache_outcome = "hit"

        additional_headers = _get_value(hidden_params, "additional_headers", {})
        attempted_retries = _reported_non_negative_integer(
            _get_value(additional_headers, "x-litellm-attempted-retries")
        )
        if attempted_retries is not None:
            request.attempted_retries = attempted_retries
        attempted_fallbacks = _reported_non_negative_integer(
            _get_value(additional_headers, "x-litellm-attempted-fallbacks")
        )
        if attempted_fallbacks is not None:
            request.attempted_fallbacks = attempted_fallbacks

    def finish_gateway_request(
        self,
        request: Optional[LiteLLMGatewayRequest],
        response: Any = None,
        exception: Optional[BaseException] = None,
    ) -> None:
        if request is None or request.finalized:
            return
        finished_at = self._clock()
        self.observe_gateway_response(request, response)
        request.finalized = True

        if exception is not None and request.attempted_retries is None and not request.fallbacks_configured:
            request.attempted_retries = _reported_non_negative_integer(_get_value(exception, "num_retries"))
        if request.attempted_fallbacks not in (None, 0):
            # LiteLLM's terminal retry header belongs to the successful fallback
            # branch, not the complete logical request.
            request.attempted_retries = None

        base_tags = {"gen_ai.operation.name": request.operation_name}
        if request.requested_model:
            base_tags["gen_ai.request.model"] = request.requested_model

        duration_tags = dict(base_tags)
        if exception is not None:
            duration_tags["error.type"] = _normalize_error_type(exception)
        self._distribution(
            "trajectory.gen_ai.gateway.request.duration",
            max(0.0, finished_at - request.started_at),
            duration_tags,
        )
        self._distribution(
            "trajectory.gen_ai.gateway.request.provider_operations",
            request.provider_operations,
            {
                **base_tags,
                "trajectory.provider.operation.coverage": "partial" if request.streaming else "complete",
            },
        )
        if request.attempted_retries is not None:
            self._distribution(
                "trajectory.gen_ai.gateway.request.retries",
                request.attempted_retries,
                base_tags,
            )
        if request.attempted_fallbacks is not None:
            self._distribution(
                "trajectory.gen_ai.gateway.request.fallbacks",
                request.attempted_fallbacks,
                base_tags,
            )
        if request.cache_outcome is not None:
            self._increment(
                "trajectory.gen_ai.gateway.cache.operations",
                1,
                {**base_tags, "trajectory.cache.outcome": request.cache_outcome},
            )

    @staticmethod
    def _resolve_model_and_provider(
        requested_model: Optional[str],
        model_map: Mapping[str, tuple[str, str]],
    ) -> tuple[Optional[str], Optional[str]]:
        if requested_model is None:
            return None, None
        resolved_model, provider = model_map.get(requested_model, (requested_model, None))
        return resolved_model or requested_model, provider

    def _distribution(self, name: str, value: float, tags: dict[str, str]) -> None:
        try:
            self.client.distribution(name, value, tags)
        except Exception:
            log.debug("Failed to emit LiteLLM metric %s", name, exc_info=True)

    def _increment(self, name: str, value: float, tags: dict[str, str]) -> None:
        try:
            self.client.increment(name, value, tags)
        except Exception:
            log.debug("Failed to emit LiteLLM metric %s", name, exc_info=True)
