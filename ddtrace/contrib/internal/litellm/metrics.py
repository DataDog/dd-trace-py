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
PROVIDER_ATTEMPT_PROFILE_REVISION = "36b8dab85bea969d8c497ec19d083511a45e7819"

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
    response_model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    cost_source: Optional[str] = None
    cost_response: Any = None
    finalized: bool = False


class LiteLLMProviderMetrics:
    """Emit the Open Trajectory provider-attempt metric profile independently of trace export."""

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
        )

    def observe_first_chunk(self, attempt: Optional[LiteLLMProviderAttempt], response: Any) -> None:
        if attempt is None or attempt.finalized:
            return
        if attempt.first_chunk_at is None:
            attempt.first_chunk_at = self._clock()
        self.observe_response(attempt, response)

    def observe_response(self, attempt: Optional[LiteLLMProviderAttempt], response: Any) -> None:
        if attempt is None or attempt.finalized or response is None:
            return
        if isinstance(response, list):
            for item in response:
                self.observe_response(attempt, item)
            return

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
            hidden_params = _get_value(response, "_hidden_params", {})
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
        if attempt.cost_usd is not None and attempt.cost_source is not None:
            self._distribution(
                "trajectory.gen_ai.client.operation.cost",
                attempt.cost_usd,
                {**base_tags, "trajectory.cost.source": attempt.cost_source},
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
            log.debug("Failed to emit LiteLLM provider-attempt metric %s", name, exc_info=True)
