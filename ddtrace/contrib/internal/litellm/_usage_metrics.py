"""Usage and cost metrics for calls made through a LiteLLM proxy.

A LiteLLM ``CustomLogger`` builds two kinds of plain observations, which the native ``ai_usage``
module turns into OpenTelemetry metric points:

- one per *provider attempt*, a request from the proxy to a model provider. Retries and fallbacks are
  separate attempts. It is projected under ``gen_ai.client.provider_attempt@0.1.0`` and, when usage is
  known, ``trajectory.gen_ai.client.token_breakdown@0.1.0``;
- one per *gateway request*, a request from a client to the proxy, projected under
  ``trajectory.gen_ai.gateway.request@0.1.0``.

The adapter only copies fields. It does no arithmetic on usage, knows no metric names, and never reads
prompts, responses, keys or key hashes.

How LiteLLM's hooks map to the two grains (verified against a running proxy, litellm 1.104):

- ``async_pre_call_hook`` starts a gateway request, keyed by ``litellm_call_id``, which every later hook
  of the same client request carries.
- ``log_pre_api_call`` fires once per provider attempt, retries and fallbacks included, and never for a
  gateway cache hit. The attempts of one request run one after the other.
- An attempt that fails before streaming ends with ``async_post_call_failure_deployment_hook``. One that
  fails mid-stream ends with ``async_log_failure_event``. The request's final attempt ends with its one
  ``async_log_success_event``, which also ends the gateway request.
- ``async_post_call_failure_hook`` ends a gateway request that failed for good.
"""

from __future__ import annotations

from collections import OrderedDict
import datetime
import json
import re
import threading
import time
from typing import Any
from typing import Optional

import litellm
from litellm.integrations.custom_logger import CustomLogger

from ddtrace.internal.logger import get_logger
from ddtrace.trace import tracer


log = get_logger(__name__)

PROVIDER_ATTEMPT = "gen_ai.client.provider_attempt@0.1.0"
TOKEN_BREAKDOWN = "trajectory.gen_ai.client.token_breakdown@0.1.0"
GATEWAY_REQUEST = "trajectory.gen_ai.gateway.request@0.1.0"

# The span attribute that lists the profiles recorded for a call, so the backend does not derive them again.
RECORDED_PROFILES_TAG = "trajectory.metrics.recorded"
ATTEMPT_PROFILES = ",".join((PROVIDER_ATTEMPT, TOKEN_BREAKDOWN))
GATEWAY_PROFILES = GATEWAY_REQUEST

# The metrics exported by default. The profiles define more, which can be added here later.
DEFAULT_METRICS = (
    "gen_ai.client.inference.duration",
    "gen_ai.client.operation.duration",
    "gen_ai.client.inference.usage.input_tokens",
    "gen_ai.client.inference.usage.output_tokens",
    "gen_ai.client.inference.usage.cache_read.input_tokens",
    "gen_ai.client.inference.usage.cache_write.input_tokens",
    "trajectory.gen_ai.client.inference.usage.cost",
    "trajectory.gen_ai.gateway.request.estimated_cost",
)

_OPERATIONS = {
    "completion": "chat",
    "acompletion": "chat",
    "responses": "chat",
    "aresponses": "chat",
    "anthropic_messages": "chat",
    "text_completion": "text_completion",
    "atext_completion": "text_completion",
    "embedding": "embeddings",
    "aembedding": "embeddings",
}

# LiteLLM's provider names that differ from the OpenTelemetry well-known values.
_PROVIDERS = {
    "text-completion-openai": "openai",
    "anthropic_text": "anthropic",
    "bedrock": "aws.bedrock",
    "bedrock_converse": "aws.bedrock",
    "azure": "azure.ai.openai",
    "azure_text": "azure.ai.openai",
    "azure_ai": "azure.ai.inference",
    "vertex_ai": "gcp.vertex_ai",
    "vertex_ai_beta": "gcp.vertex_ai",
    "gemini": "gcp.gemini",
    "cohere_chat": "cohere",
    "watsonx": "ibm.watsonx.ai",
    "watsonx_text": "ibm.watsonx.ai",
    "xai": "x_ai",
    "mistral": "mistral_ai",
    "moonshot": "moonshot_ai",
}

# Opt-in tags: (configuration name) -> (attribute, LiteLLM metadata key).
_OPT_IN_METADATA_TAGS = {
    "user": ("user.id", "user_api_key_user_id"),
    "team": ("trajectory.team.id", "user_api_key_team_id"),
    "key_alias": ("trajectory.gateway.key.alias", "user_api_key_alias"),
}
OPT_IN_TAGS = frozenset(_OPT_IN_METADATA_TAGS) | {"route", "destination", "service", "host"}
_IDENTITY_KEYS = tuple(key for _, key in _OPT_IN_METADATA_TAGS.values())
_STREAM_WRAPPER_CALLS = frozenset({"completion", "acompletion", "text_completion", "atext_completion"})

# Requests that never end (a lost callback) are forgotten after this long, and at most this many are kept.
_REQUEST_TTL_SECONDS = 15 * 60
_MAX_REQUESTS = 10_000

_IDENTIFIER_INVALID = re.compile(r"[^a-z0-9_.-]")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _identifier(value: Any) -> Optional[str]:
    """A low-cardinality identifier: lower case, ``[a-z0-9_.-]``, starting with a letter."""
    if not isinstance(value, str) or not value:
        return None
    text = _IDENTIFIER_INVALID.sub("_", value.strip().lower())
    return text if text[:1].isalpha() else None


def operation_name(call_type: Any) -> Optional[str]:
    if call_type is None:
        return None
    name = getattr(call_type, "value", call_type)
    if not isinstance(name, str):
        return None
    return _OPERATIONS.get(name.rpartition(".")[2])


def provider_name(custom_llm_provider: Any) -> Optional[str]:
    if not isinstance(custom_llm_provider, str):
        return None
    return _PROVIDERS.get(custom_llm_provider) or _identifier(custom_llm_provider)


def error_type(error_class: Any) -> Optional[str]:
    """``RateLimitError`` -> ``rate_limit_error``."""
    if isinstance(error_class, BaseException):
        error_class = type(error_class).__name__
    if not isinstance(error_class, str) or not error_class:
        return None
    return _identifier(_CAMEL_BOUNDARY.sub("_", error_class))


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def usage_fields(usage: Any, embeddings: bool) -> dict[str, Any]:
    """The token fields of an observation from LiteLLM's usage object.

    LiteLLM normalizes usage to the Chat Completions shape on every route, including the Anthropic Messages and
    Responses routes: ``prompt_tokens`` includes cached input and cache writes, and ``completion_tokens``
    includes reasoning. A usage object of zeros is LiteLLM's placeholder for unknown usage.
    """
    if usage is None:
        return {}
    input_tokens = _count(_get(usage, "prompt_tokens"))
    output_tokens = _count(_get(usage, "completion_tokens"))
    if not input_tokens and not output_tokens:
        return {}
    fields: dict[str, Any] = {"input_tokens": input_tokens, "input_basis": "includes_cache"}
    if not embeddings:
        fields["output_tokens"] = output_tokens
        fields["output_basis"] = "includes_reasoning"
    prompt_details = _get(usage, "prompt_tokens_details")
    cache_read = _count(_get(usage, "cache_read_input_tokens"))
    if cache_read is None:
        cache_read = _count(_get(prompt_details, "cached_tokens"))
    if cache_read is not None:
        fields["cache_read_input_tokens"] = cache_read
    cache_write = _count(_get(usage, "cache_creation_input_tokens"))
    if cache_write is None:
        cache_write = _count(_get(prompt_details, "cache_creation_tokens"))
    if cache_write is not None:
        fields["cache_write_input_tokens"] = cache_write
    lifetimes = _get(prompt_details, "cache_creation_token_details") or _get(usage, "cache_creation")
    for key, field in (
        ("ephemeral_5m_input_tokens", "cache_write_5m_input_tokens"),
        ("ephemeral_1h_input_tokens", "cache_write_1h_input_tokens"),
    ):
        value = _count(_get(lifetimes, key))
        if value is not None:
            fields[field] = value
    if not embeddings:
        reasoning = _count(_get(_get(usage, "completion_tokens_details"), "reasoning_tokens"))
        if reasoning is not None:
            fields["reasoning_output_tokens"] = reasoning
    return {key: value for key, value in fields.items() if value is not None}


def _priced_at_zero(kwargs: dict[str, Any]) -> bool:
    """Whether a zero cost is a real zero: the deployment or LiteLLM's price map prices the model at zero.

    LiteLLM reports a cost of zero both for a model priced at zero and for a model it has no price for.
    """
    params = kwargs.get("litellm_params") or {}
    configured = (params.get("input_cost_per_token"), params.get("output_cost_per_token"))
    if all(price is not None for price in configured):
        return all(price == 0 for price in configured)
    entry = getattr(litellm, "model_cost", {}).get(kwargs.get("model") or "")
    if not isinstance(entry, dict):
        return False
    prices = (entry.get("input_cost_per_token"), entry.get("output_cost_per_token"))
    return all(price is not None for price in prices) and all(price == 0 for price in prices)


def _response_model(kwargs: dict[str, Any]) -> Optional[str]:
    """The model the provider's response names. LiteLLM rewrites the response's model to the client's route,
    so read the provider's own response when it is available.
    """
    original = kwargs.get("original_response")
    if isinstance(original, str) and original.startswith("{"):
        try:
            original = json.loads(original)
        except ValueError:
            return None
    if isinstance(original, dict):
        model = original.get("model")
        return model if isinstance(model, str) and model else None
    return None


def _metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The request metadata: under ``litellm_params`` in callback kwargs, at the top of deployment hook data."""
    metadata = (kwargs.get("litellm_params") or {}).get("metadata") or kwargs.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _seconds(start: Any, end: Any) -> Optional[float]:
    if isinstance(start, datetime.datetime) and isinstance(end, datetime.datetime):
        try:
            return max((end - start).total_seconds(), 0.0)
        except TypeError:
            return None
    return None


class _Attempt:
    __slots__ = (
        "start",
        "operation",
        "provider",
        "model",
        "stream",
        "checked_stream",
        "model_group",
        "model_id",
        "closed",
        "streaming",
    )

    def __init__(self, kwargs: dict[str, Any]) -> None:
        metadata = _metadata(kwargs)
        call_type = kwargs.get("call_type")
        self.start = kwargs.get("api_call_start_time") or datetime.datetime.now()
        self.operation = operation_name(call_type)
        # Chat and text completion streams pass through LiteLLM's CustomStreamWrapper, where the integration sees
        # whether a provider chunk carried usage. Without one, LiteLLM counts the tokens itself and does not say so.
        # The Anthropic Messages and Responses streams carry usage in their own final events.
        self.checked_stream = str(getattr(call_type, "value", call_type)).rpartition(".")[2] in _STREAM_WRAPPER_CALLS
        self.provider = provider_name(kwargs.get("custom_llm_provider"))
        self.model = kwargs.get("model") if isinstance(kwargs.get("model"), str) else None
        self.stream = bool(kwargs.get("stream"))
        self.model_group = metadata.get("model_group")
        self.model_id = (metadata.get("model_info") or {}).get("id") or kwargs.get("model_id")
        self.closed = False
        # Whether the stream wrapper saw a chunk of this attempt.
        self.streaming = False


class _Request:
    __slots__ = (
        "start",
        "created",
        "operation",
        "route",
        "identity",
        "attempts",
        "provider_usage",
        "ended",
    )

    def __init__(self) -> None:
        self.start: Optional[float] = None
        self.created = time.monotonic()
        self.operation: Optional[str] = None
        self.route: Optional[str] = None
        self.identity: dict[str, Any] = {}
        self.attempts: list[_Attempt] = []
        # Whether the provider reported usage in the stream of the latest attempt.
        self.provider_usage = False
        self.ended = False


class UsageMetricsLogger(CustomLogger):
    """Builds usage metric observations from LiteLLM proxy hooks and hands them to a writer.

    Every hook catches its own errors: a failure here never affects the proxied request.
    """

    def __init__(
        self, writer: Any, tags: frozenset[str], client_source: Optional[str], resource: dict[str, str]
    ) -> None:
        super().__init__()
        self._writer = writer
        self._tags = tags
        self._client_source = client_source
        self._resource = resource
        self._requests: OrderedDict[str, _Request] = OrderedDict()
        self._lock = threading.Lock()

    # Request tracking

    def _request(self, call_id: Any, create: bool = True) -> Optional[_Request]:
        if not isinstance(call_id, str):
            return None
        with self._lock:
            request = self._requests.get(call_id)
            if request is None and create:
                self._evict()
                request = self._requests[call_id] = _Request()
            return request

    def _evict(self) -> None:
        cutoff = time.monotonic() - _REQUEST_TTL_SECONDS
        while self._requests:
            call_id, oldest = next(iter(self._requests.items()))
            if len(self._requests) < _MAX_REQUESTS and oldest.created >= cutoff:
                break
            del self._requests[call_id]

    def _forget_if_done(self, call_id: str, request: _Request) -> None:
        if request.ended and all(attempt.closed for attempt in request.attempts):
            with self._lock:
                self._requests.pop(call_id, None)

    @staticmethod
    def _open_attempt(request: _Request, kwargs: dict[str, Any]) -> Optional[_Attempt]:
        """The open attempt an event belongs to: the one that started at the event's ``api_call_start_time``,
        else the latest open one.
        """
        start = kwargs.get("api_call_start_time")
        for attempt in reversed(request.attempts):
            if not attempt.closed and start is not None and attempt.start == start:
                return attempt
        return UsageMetricsLogger._latest_open_attempt(request)

    @staticmethod
    def _latest_open_attempt(request: _Request) -> Optional[_Attempt]:
        for attempt in reversed(request.attempts):
            if not attempt.closed:
                return attempt
        return None

    def mark_stream_chunk(self, call_id: Any, has_usage: bool) -> None:
        """Called by the stream wrapper for each provider chunk of a chat or text completion stream."""
        request = self._request(call_id, create=False)
        if request is None:
            return
        if request.attempts:
            request.attempts[-1].streaming = True
        if has_usage:
            request.provider_usage = True

    def attempt_failed(self, kwargs: dict[str, Any], exception: BaseException) -> None:
        """Called by the integration's wrapper when a chat or text completion attempt raises before streaming.

        Attempts of one request run one after the other, so the failed attempt is the latest open one. On LiteLLM
        versions that call ``async_post_call_failure_deployment_hook``, that hook has already closed it.
        """
        try:
            call_id = kwargs.get("litellm_call_id")
            request = self._request(call_id, create=False)
            if request is None or not isinstance(call_id, str):
                return
            attempt = self._latest_open_attempt(request)
            if attempt is not None:
                self._record_attempt(
                    request, attempt, kwargs, datetime.datetime.now(), error_type(exception), None, False
                )
                self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: attempt_failed failed", exc_info=True)

    # Deployment attributes

    def _deployment_attributes(
        self, metadata: dict[str, Any], route: Optional[str], destination: Any
    ) -> dict[str, str]:
        attributes = dict(self._resource)
        for tag, (attribute, key) in _OPT_IN_METADATA_TAGS.items():
            value = metadata.get(key)
            if tag in self._tags and isinstance(value, str) and value:
                attributes[attribute] = value
        if "route" in self._tags and isinstance(route, str) and route:
            attributes["trajectory.gateway.route"] = route
        if "destination" in self._tags and isinstance(destination, str) and destination:
            attributes["trajectory.gateway.destination.id"] = destination
        if self._client_source:
            attributes["trajectory.client_source"] = self._client_source
        return attributes

    # Records

    def _record_attempt(
        self,
        request: _Request,
        attempt: _Attempt,
        kwargs: dict[str, Any],
        end: Any,
        error: Optional[str],
        payload: Optional[dict[str, Any]],
        estimated: bool,
        response: Any = None,
    ) -> None:
        attempt.closed = True
        if attempt.operation is None or attempt.provider is None:
            return
        duration = _seconds(attempt.start, end)
        if duration is None:
            return
        observation: dict[str, Any] = {
            "operation_name": attempt.operation,
            "provider_name": attempt.provider,
            "duration_seconds": duration,
            "streaming": attempt.stream,
            "observation_point": "gateway",
        }
        if attempt.model:
            observation["request_model"] = attempt.model
        if error:
            observation["error_type"] = error
        if payload is not None:
            response_model = _response_model(kwargs)
            if response_model:
                observation["response_model"] = response_model
            if attempt.stream:
                first_chunk = _seconds(attempt.start, kwargs.get("completion_start_time"))
                if first_chunk is not None and first_chunk <= duration:
                    observation["time_to_first_chunk_seconds"] = first_chunk
            # LiteLLM 1.6x has no usage_object; the response's own usage is the object it is made from.
            usage_object = (payload.get("metadata") or {}).get("usage_object")
            if usage_object is None:
                usage_object = getattr(response, "usage", None)
            usage = usage_fields(usage_object, attempt.operation == "embeddings")
            observation.update(usage)
            unreported_stream = attempt.stream and attempt.checked_stream and not request.provider_usage
            if usage and (estimated or unreported_stream):
                observation["input_token_source"] = "estimated"
                observation["output_token_source"] = "estimated"
            cost = payload.get("response_cost")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and (cost > 0 or _priced_at_zero(kwargs)):
                observation["cost_usd"] = float(cost)
                observation["cost_source"] = "estimated"
        metadata = _metadata(kwargs)
        identity = dict(request.identity)
        identity.update({key: metadata[key] for key in _IDENTITY_KEYS if metadata.get(key)})
        attributes = self._deployment_attributes(
            identity, request.route or metadata.get("original_model_group"), attempt.model_id
        )
        self._writer.record(PROVIDER_ATTEMPT, observation, attributes)
        if "input_tokens" in observation:
            self._writer.record(TOKEN_BREAKDOWN, observation, attributes)

    def _record_request(
        self,
        call_id: str,
        request: _Request,
        kwargs: dict[str, Any],
        error: Optional[str],
        cache_hit: bool,
        cost: Any,
    ) -> None:
        if request.ended:
            return
        request.ended = True
        if request.start is None or request.operation is None:
            return
        observation: dict[str, Any] = {
            "operation_name": request.operation,
            "duration_seconds": max(time.monotonic() - request.start, 0.0),
            "observation_point": "gateway",
            "provider_operations": len(request.attempts),
            "provider_operation_coverage": "complete",
        }
        if request.route:
            observation["request_model"] = request.route
        groups = [attempt.model_group for attempt in request.attempts]
        fallbacks = sum(1 for previous, current in zip(groups, groups[1:]) if current != previous)
        observation["fallbacks"] = fallbacks
        if not fallbacks and request.attempts:
            # Retries are counted only on a request that did not fall back.
            observation["retries"] = len(request.attempts) - 1
        if cache_hit:
            observation["cache_outcomes"] = ["hit"]
            observation["estimated_cost_usd"] = 0
        elif isinstance(cost, (int, float)) and not isinstance(cost, bool) and (cost > 0 or _priced_at_zero(kwargs)):
            observation["estimated_cost_usd"] = float(cost)
        if error:
            observation["error_type"] = error
        destination = request.attempts[-1].model_id if request.attempts else None
        attributes = self._deployment_attributes(request.identity, request.route, destination)
        self._writer.record(GATEWAY_REQUEST, observation, attributes)

    # LiteLLM hooks. Each catches its own errors and returns what LiteLLM expects from a no-op hook.

    async def async_pre_call_hook(self, user_api_key_dict: Any, cache: Any, data: Any, call_type: Any) -> Any:
        try:
            # Older proxies assign the call id only after this hook; their requests start at their first call.
            request = self._request(data.get("litellm_call_id"))
            if request is not None and request.start is None:
                request.start = time.monotonic()
                request.operation = operation_name(call_type)
                route = data.get("model")
                request.route = route if isinstance(route, str) else None
                request.identity = {
                    "user_api_key_user_id": getattr(user_api_key_dict, "user_id", None),
                    "user_api_key_team_id": getattr(user_api_key_dict, "team_id", None),
                    "user_api_key_alias": getattr(user_api_key_dict, "key_alias", None),
                }
        except Exception:
            log.debug("LiteLLM usage metrics: async_pre_call_hook failed", exc_info=True)
        return None

    def has_request(self, call_id: Any) -> bool:
        request = self._request(call_id, create=False)
        return request is not None and request.start is not None

    def _proxy_request(self, kwargs: dict[str, Any], elapsed: float = 0.0) -> Optional[_Request]:
        """The request a call belongs to. Only calls made by the proxy are recorded.

        Proxies that assign the call id after ``async_pre_call_hook`` (LiteLLM 1.6x) have no request yet at their
        first call: it starts there, ``elapsed`` seconds ago, with its route and identity from the call's metadata.
        """
        call_id = kwargs.get("litellm_call_id")
        request = self._request(call_id, create=False)
        if request is not None or not isinstance(call_id, str):
            return request
        if not (kwargs.get("litellm_params") or {}).get("proxy_server_request"):
            return None
        request = self._request(call_id)
        if request is not None and request.start is None:
            metadata = _metadata(kwargs)
            request.start = time.monotonic() - max(elapsed, 0.0)
            request.operation = operation_name(kwargs.get("call_type"))
            route = metadata.get("original_model_group") or metadata.get("model_group")
            request.route = route if isinstance(route, str) else None
            request.identity = {key: metadata.get(key) for key in _IDENTITY_KEYS}
        return request

    def log_pre_api_call(self, model: Any, messages: Any, kwargs: Any) -> None:
        try:
            request = self._proxy_request(kwargs)
            if request is not None:
                request.attempts.append(_Attempt(kwargs))
                request.provider_usage = False
                _mark_current_span(ATTEMPT_PROFILES)
        except Exception:
            log.debug("LiteLLM usage metrics: log_pre_api_call failed", exc_info=True)

    async def async_post_call_failure_deployment_hook(self, request_data: Any, *args: Any, **kwargs: Any) -> None:
        try:
            exception = kwargs.get("exception", kwargs.get("original_exception", args[0] if args else None))
            call_id = request_data.get("litellm_call_id")
            request = self._request(call_id, create=False)
            if request is None:
                return None
            attempt = self._latest_open_attempt(request)
            if attempt is not None:
                self._record_attempt(
                    request, attempt, request_data, datetime.datetime.now(), error_type(exception), None, False
                )
                self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: async_post_call_failure_deployment_hook failed", exc_info=True)
        return None

    async def async_log_success_event(self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any) -> None:
        try:
            call_id = kwargs.get("litellm_call_id")
            request = self._proxy_request(kwargs, _seconds(start_time, end_time) or 0.0)
            payload = kwargs.get("standard_logging_object") or {}
            if request is None:
                return
            cache_hit = payload.get("cache_hit") is True
            error_information = payload.get("error_information") or {}
            # A client that disconnects mid-stream ends the request with a success event and partial usage.
            error = error_type(error_information.get("error_class"))
            if not cache_hit:
                attempt = self._open_attempt(request, kwargs)
                if attempt is not None:
                    self._record_attempt(
                        request, attempt, kwargs, end_time, error, payload, error is not None, response_obj
                    )
            self._record_request(call_id, request, kwargs, error, cache_hit, payload.get("response_cost"))
            self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: async_log_success_event failed", exc_info=True)

    async def async_log_failure_event(self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any) -> None:
        # LiteLLM calls this once per request for non-streamed calls and once per attempt for streamed ones, and
        # possibly after the next attempt started. An attempt that fails before streaming is closed by the wrapper
        # or by async_post_call_failure_deployment_hook, so only an attempt that is still open and matches this
        # event's start time is closed here: a failure mid-stream, or one of a route the wrapper does not see.
        try:
            call_id = kwargs.get("litellm_call_id")
            request = self._request(call_id, create=False)
            if request is None:
                return
            start = kwargs.get("api_call_start_time")
            attempt = next(
                (a for a in reversed(request.attempts) if not a.closed and start is not None and a.start == start),
                None,
            )
            if attempt is None or (attempt.checked_stream and not attempt.streaming):
                return
            payload = kwargs.get("standard_logging_object") or {}
            error_information = payload.get("error_information") or {}
            error = error_type(error_information.get("error_class") or kwargs.get("exception")) or "error"
            self._record_attempt(request, attempt, kwargs, end_time, error, payload, True)
            self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: async_log_failure_event failed", exc_info=True)

    async def async_post_call_failure_hook(
        self, request_data: Any, original_exception: Any, user_api_key_dict: Any, traceback_str: Any = None
    ) -> Any:
        try:
            call_id = request_data.get("litellm_call_id")
            request = self._request(call_id, create=False)
            if request is not None:
                self._record_request(call_id, request, request_data, error_type(original_exception), False, None)
                self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: async_post_call_failure_hook failed", exc_info=True)
        return None


def _mark_current_span(profiles: str) -> None:
    """Mark the active provider call's span, and the Router span of its gateway request above it."""
    span = tracer.current_span()
    if span is None or span.name != "litellm.request":
        return
    span._set_attribute(RECORDED_PROFILES_TAG, profiles)
    parent = span._parent
    if parent is not None and parent.name == "litellm.request" and str(parent.resource).startswith("router."):
        parent._set_attribute(RECORDED_PROFILES_TAG, GATEWAY_PROFILES)
