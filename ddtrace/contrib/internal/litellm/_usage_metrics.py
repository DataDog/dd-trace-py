"""Usage and cost metrics for calls made through a LiteLLM proxy.

A LiteLLM CustomLogger builds two kinds of plain observations, which the native ai_usage
module turns into OpenTelemetry metric points:

- one per provider attempt, a request from the proxy to a model provider. Retries and fallbacks are
  separate attempts. It is projected under gen_ai.client.provider_attempt@0.1.0 and, when usage is
  known, trajectory.gen_ai.client.token_breakdown@0.1.0;
- one per gateway request, a request from a client to the proxy, projected under
  trajectory.gen_ai.gateway.request@0.1.0.

The adapter only copies fields. It does no arithmetic on usage, knows no metric names, and never reads
prompts, responses, keys or key hashes.

The provider account and project an attempt was billed to come from, in order: the ids the operator declared in the
deployment's model_info; the provider's own response headers (Anthropic's anthropic-organization-id, OpenAI's
openai-organization and openai-project), of which only these are read; and a Bedrock model given as an ARN, which
names the AWS account. LiteLLM keeps the provider's headers in the response's hidden additional_headers, prefixed
llm_provider-, on every route but native Anthropic Messages streams, whose headers are only on the call's
httpx_response.

How LiteLLM's hooks map to the two grains (verified against a running proxy, litellm 1.104):

- async_pre_call_hook starts a gateway request, keyed by litellm_call_id, which every later hook
  of the same client request carries.
- log_pre_api_call fires once per provider attempt, retries and fallbacks included, and never for a
  gateway cache hit. The attempts of one request run one after the other.
- An attempt that fails before streaming ends with async_post_call_failure_deployment_hook. One that
  fails mid-stream ends with async_log_failure_event. The request's final attempt ends with its one
  async_log_success_event, which also ends the gateway request.
- async_post_call_failure_hook ends a gateway request that failed for good, and any attempt still open.
- Which token counts a provider reported in a stream is seen in the stream itself: in
  CustomStreamWrapper.chunk_creator for chat and text completion streams, and in the raw events of native
  Anthropic Messages and Responses streams.
"""

from __future__ import annotations

from collections import OrderedDict
import datetime
import time
from typing import Any
from typing import Optional

import litellm
from litellm.integrations.custom_logger import CustomLogger


try:
    from litellm.llms.bedrock.common_utils import BedrockModelInfo
except ImportError:
    BedrockModelInfo = None  # type: ignore[misc, assignment, unused-ignore]

try:
    from litellm.litellm_core_utils.llm_cost_calc.guardrail_cost import guardrail_information_cost
except ImportError:
    # LiteLLM before 1.104 charges nothing for a failed request.
    guardrail_information_cost = None  # type: ignore[assignment, unused-ignore]

from ddtrace.contrib.internal.litellm._usage_metrics_writer import ai_usage
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.trace import tracer


log = get_logger(__name__)

PROVIDER_ATTEMPT = "gen_ai.client.provider_attempt@0.1.0"
TOKEN_BREAKDOWN = "trajectory.gen_ai.client.token_breakdown@0.1.0"  # nosec B105: a profile id, not a secret
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

# Opt-in tags: (configuration name) -> (attribute, LiteLLM metadata keys, the first one set wins). user.id is only
# ever a person: the email of the key's owner, else the end user the request was made for; never the key owner's user
# id, which can be a service account or a team. A non-human caller is told apart by its key alias.
_OPT_IN_METADATA_TAGS = {
    "user": ("user.id", ("user_api_key_user_email", "user_api_key_end_user_id")),
    "team": ("trajectory.team.id", ("user_api_key_team_id",)),
    "key_alias": ("trajectory.gateway.key.alias", ("user_api_key_alias",)),
}
_IDENTITY_KEYS = tuple(key for _, keys in _OPT_IN_METADATA_TAGS.values() for key in keys)

PROVIDER_ACCOUNT_ID = "trajectory.request.provider_account_id"
PROJECT_ID = "trajectory.request.project_id"
API_KEY_ID = "trajectory.request.api_key_id"
# Opt-in tags for the ids the integration finds itself: (configuration name) -> attribute.
_OPT_IN_PROVIDER_ID_TAGS = {"provider_account": PROVIDER_ACCOUNT_ID, "project": PROJECT_ID}
OPT_IN_TAGS = frozenset((*_OPT_IN_METADATA_TAGS, *_OPT_IN_PROVIDER_ID_TAGS, "route", "destination", "service", "host"))

# The ids an operator declares in a deployment's model_info, for what the integration cannot find: (model_info key,
# attribute). They are copied as written, and sent whenever declared. The provider's API key id is only ever one of
# these: it is never derived from a key.
_OPERATOR_IDS = (
    ("datadog_api_key_id", API_KEY_ID),
    ("datadog_provider_account_id", PROVIDER_ACCOUNT_ID),
    ("datadog_project_id", PROJECT_ID),
)
_MAX_ID_LENGTH = 128

# Providers whose usage LiteLLM counts itself on a call that is not streamed: it gets no count from them, or fills in
# a missing one in a way a callback cannot tell apart from a reported count (Ollama, and Bedrock's invoke route).
_LOCALLY_COUNTED_PROVIDERS = frozenset(
    {"replicate", "sagemaker", "langgraph", "predibase", "anthropic_text", "petals", "ollama", "ollama_chat"}
)
_GEMINI_PROVIDERS = frozenset({"gcp.gemini", "gcp.vertex_ai"})
# Google's OpenAI-compatible Gemini endpoint, reached through an openai/ deployment with this base URL.
_GEMINI_COMPAT_HOST = "generativelanguage.googleapis.com"
# The error class LiteLLM's proxy gives a stream whose client left before the proxy's own generator finished.
_CLIENT_DISCONNECTED = "ClientDisconnected"

# Requests that never end (a lost callback) are forgotten after this long, and at most this many are kept.
_REQUEST_TTL_SECONDS = 15 * 60
_MAX_REQUESTS = 10_000

# The error.type of a failure nothing names.
_UNKNOWN_ERROR = "api_error"


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
    return ai_usage.gen_ai_provider_name(custom_llm_provider)


def error_type(error: Any, status: Any = None) -> Optional[str]:
    """The shared error.type of an exception, or of an error class name, and an HTTP status."""
    if isinstance(error, BaseException):
        status = getattr(error, "status_code", None) if status is None else status
        error = type(error).__name__
    name = error if isinstance(error, str) and error else None
    if isinstance(status, str) and status.isdigit():
        status = int(status)
    status = status if isinstance(status, int) and not isinstance(status, bool) else None
    if name is None and status is None:
        return None
    return ai_usage.classify_error(name, status)


def _count(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _plain(value: Any, depth: int = 0) -> Any:
    """A LiteLLM object as plain JSON values (dict, list, str, int, float, bool, None), for the native module."""
    if value is None or isinstance(value, bool) or depth > 32:
        return value if depth <= 32 else None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item, depth + 1) for key, item in value.items() if isinstance(key, str)}
    if isinstance(value, (list, tuple)):
        return [_plain(item, depth + 1) for item in value]
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _plain(dump(), depth + 1)
        except Exception:
            return None
    members = getattr(value, "__dict__", None)
    return _plain(members, depth + 1) if isinstance(members, dict) else None


def usage_fields(
    usage: Any,
    embeddings: bool,
    modality: bool = True,
    drop_reasoning: bool = False,
    usage_format: str = "auto",
) -> dict[str, Any]:
    """The token fields of an observation from LiteLLM's usage object, read by the native module's shared rules.

    LiteLLM normalizes usage to the Chat Completions shape on every route; LiteLLM 1.6x hands over the response's own
    usage instead, in the Responses or native Anthropic Messages shape, which the module tells apart. A usage object
    of zeros is LiteLLM's placeholder for unknown usage. modality is False when the modality details are LiteLLM's own
    split rather than the provider's; drop_reasoning leaves out a reasoning count LiteLLM split out itself.
    """
    plain = _plain(usage)
    if not isinstance(plain, dict):
        return {}
    if drop_reasoning:
        for key in ("completion_tokens_details", "output_tokens_details"):
            details = plain.get(key)
            if isinstance(details, dict):
                details.pop("reasoning_tokens", None)
    if usage_format != "auto" and "prompt_tokens" not in plain:
        usage_format = "auto"
    fields = ai_usage.parse_provider_response(usage_format, plain, embeddings)["usage"] or {}
    if not modality:
        fields.pop("input_tokens_by_modality", None)
        fields.pop("output_tokens_by_modality", None)
    return fields


def _priced_at_zero(kwargs: dict[str, Any]) -> bool:
    """Whether a zero cost is a real zero: the deployment or LiteLLM's price map prices the model at zero.

    LiteLLM reports a cost of zero both for a model priced at zero and for a model it has no price for. An embeddings
    call has an input price only.
    """
    keys = ["input_cost_per_token"]
    if operation_name(kwargs.get("call_type")) != "embeddings":
        keys.append("output_cost_per_token")
    params = kwargs.get("litellm_params") or {}
    configured = [params.get(key) for key in keys]
    if all(price is not None for price in configured):
        return all(price == 0 for price in configured)
    entry = getattr(litellm, "model_cost", {}).get(kwargs.get("model") or "")
    if not isinstance(entry, dict):
        return False
    prices = [entry.get(key) for key in keys]
    return all(price is not None for price in prices) and all(price == 0 for price in prices)


def _original_response(kwargs: dict[str, Any]) -> dict[str, Any]:
    """What the provider's own response reported. LiteLLM rewrites the response's model to the client's route and
    normalizes its usage, so read the provider's original response when it is available.
    """
    original = kwargs.get("original_response")
    try:
        if isinstance(original, str) and original.startswith("{"):
            return ai_usage.parse_provider_body("auto", original)[0]
        if isinstance(original, dict):
            return ai_usage.parse_provider_response("auto", _plain(original))
    except Exception:
        log.debug("LiteLLM usage metrics: failed to read the provider's response", exc_info=True)
    return {}


def _response_model(kwargs: dict[str, Any]) -> Optional[str]:
    """The model the provider's response names."""
    return _original_response(kwargs).get("response_model")


def _gemini_dropped_thoughts(original: dict[str, Any], output_tokens: Any) -> Optional[int]:
    """The thinking count a Gemini response reported that LiteLLM left out of its output count, else None.

    LiteLLM adds thoughtsTokenCount to the output count, except on a response a content filter stopped, whose output
    count is candidatesTokenCount alone; LiteLLM before 1.67 never adds it. The thoughts are billed all the same: the
    provider's own output count includes them.
    """
    usage = original.get("usage") or {}
    reasoning = usage.get("reasoning_output_tokens")
    if not reasoning or not isinstance(output_tokens, int) or output_tokens + reasoning != usage.get("output_tokens"):
        return None
    return reasoning


def _gemini_compat(kwargs: dict[str, Any]) -> bool:
    """Whether a call went to Google's OpenAI-compatible Gemini endpoint, which leaves thinking out of
    completion_tokens while its total_tokens counts it.
    """
    api_base = (kwargs.get("litellm_params") or {}).get("api_base")
    return isinstance(api_base, str) and _GEMINI_COMPAT_HOST in api_base


def _metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The request metadata: under litellm_params in callback kwargs, at the top of deployment hook data."""
    metadata = (kwargs.get("litellm_params") or {}).get("metadata") or kwargs.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _model_info(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The deployment's model_info, custom keys included. LiteLLM before 1.104 keeps it only under litellm_metadata on
    the Anthropic Messages and Responses routes, and under litellm_params.model_info on every route but Responses.
    """
    params = kwargs.get("litellm_params") or {}
    for bucket in (params, _metadata(kwargs), params.get("litellm_metadata")):
        model_info = bucket.get("model_info") if isinstance(bucket, dict) else None
        if isinstance(model_info, dict) and model_info:
            return model_info
    return {}


def _billing_id(value: Any) -> Optional[str]:
    """An account, project or key id as written: a non-empty string of bounded length."""
    return value if isinstance(value, str) and 0 < len(value) <= _MAX_ID_LENGTH and value.strip() else None


def _operator_ids(model_info: dict[str, Any]) -> dict[str, str]:
    """The ids the operator declared in the deployment's model_info."""
    ids = {}
    for key, attribute in _OPERATOR_IDS:
        value = _billing_id(model_info.get(key))
        if value is not None:
            ids[attribute] = value
    return ids


def _bedrock_account(*models: Any) -> Optional[str]:
    """The AWS account of the first Bedrock model ARN among models."""
    for model in models:
        account = ai_usage.bedrock_arn_account(model) if isinstance(model, str) else None
        if account is not None:
            return account
    return None


def _header(headers: Any, name: str) -> Optional[str]:
    """One header's value from a mapping of headers. httpx gives header names in lower case."""
    get = getattr(headers, "get", None)
    if get is None:
        return None
    try:
        return _billing_id(get(name))
    except Exception:
        return None


def _response_header(
    name: str, kwargs: dict[str, Any], payload: Optional[dict[str, Any]], response: Any
) -> Optional[str]:
    """One of the provider's response headers, wherever this LiteLLM version keeps it for the callback."""
    prefixed = "llm_provider-" + name
    hidden = getattr(response, "_hidden_params", None)
    if isinstance(hidden, dict):
        value = _header(hidden.get("additional_headers"), prefixed)
        if value is not None:
            return value
    payload_hidden = (payload or {}).get("hidden_params")
    if isinstance(payload_hidden, dict):
        value = _header(payload_hidden.get("additional_headers"), prefixed)
        if value is not None:
            return value
    value = _header(kwargs.get("response_headers"), name)
    if value is not None:
        return value
    return _header(getattr(kwargs.get("httpx_response"), "headers", None), name)


def service_tier(payload: dict[str, Any]) -> Optional[str]:
    """The tier the provider served the call at, as LiteLLM's payload states it: the response's own service_tier
    (OpenAI's shapes, Bedrock), else the usage object's (Anthropic). The tier a request asked for is never used.
    """
    response = payload.get("response")
    tier = _get(response, "service_tier") if isinstance(response, dict) else None
    if not isinstance(tier, str) or not tier:
        tier = _get((payload.get("metadata") or {}).get("usage_object"), "service_tier")
    return valid_service_tier(tier)


def valid_service_tier(tier: Any) -> Optional[str]:
    return tier if isinstance(tier, str) and ai_usage.is_service_tier(tier) else None


def _now_like(start: Any) -> datetime.datetime:
    """Now, in the time zone of start: LiteLLM's timestamps can be timezone-aware, and an aware and a naive
    datetime cannot be subtracted.
    """
    return datetime.datetime.now(start.tzinfo if isinstance(start, datetime.datetime) else None)


def _seconds(start: Any, end: Any) -> Optional[float]:
    if isinstance(start, datetime.datetime) and isinstance(end, datetime.datetime):
        try:
            return max((end - start).total_seconds(), 0.0)
        except TypeError:
            return None
    return None


def _bedrock_invoke(model: Optional[str]) -> bool:
    """Whether a Bedrock model is called through the invoke route, whose missing counts LiteLLM fills in itself."""
    if BedrockModelInfo is None or not model:
        return False
    try:
        return bool(BedrockModelInfo.get_bedrock_route(model) == "invoke")
    except Exception:
        return False


def _charged_on_failure(request_data: dict[str, Any]) -> float:
    """What the proxy charged a request that failed, as LiteLLM 1.104 computes it: the cost of what a stream produced
    before it broke, which it recovers into the request data, plus the billed cost of the guardrails that ran.
    Earlier versions charge nothing.
    """
    charge = 0.0
    usage_class = getattr(litellm, "Usage", None)
    cost = request_data.get("response_cost")
    if (
        usage_class is not None
        and isinstance(request_data.get("combined_usage_object"), usage_class)
        and isinstance(cost, (int, float))
        and not isinstance(cost, bool)
    ):
        charge = max(float(cost), 0.0)
    if guardrail_information_cost is not None:
        guardrails = None
        for key in ("metadata", "litellm_metadata"):
            bucket = request_data.get(key)
            if isinstance(bucket, dict) and bucket.get("standard_logging_guardrail_information") is not None:
                guardrails = bucket["standard_logging_guardrail_information"]
                break
        try:
            charge += max(float(guardrail_information_cost(guardrails)), 0.0)
        except Exception:
            log.debug("LiteLLM usage metrics: failed to price the guardrails of a failed request", exc_info=True)
    return charge


def _failure_payload(request_data: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The usage and cost of the attempt a terminal failure ends. LiteLLM 1.104 lifts a broken stream's recovered
    usage and cost into the request data even when its logging payload does not exist yet.
    """
    payload = request_data.get("standard_logging_object")
    payload = payload if isinstance(payload, dict) else None
    usage = request_data.get("combined_usage_object")
    if usage is None or (payload is not None and (payload.get("metadata") or {}).get("usage_object") is not None):
        return payload
    return {"response_cost": request_data.get("response_cost"), "metadata": {"usage_object": usage}}


def anthropic_stream_usage(chunks: Any) -> tuple[bool, bool, Optional[str], Optional[str], bool]:
    """Which sides of a native Anthropic Messages stream the provider reported, the model and service tier it
    named, and whether the provider ended the stream with message_stop.

    message_start carries the input count and a placeholder output count; message_delta carries the output count,
    just before message_stop. A stream that ends without one leaves LiteLLM to estimate that side.
    """
    response, outcome, _ = ai_usage.parse_provider_stream("anthropic", list(chunks))
    usage = response["usage"] or {}
    completed = outcome == "completed"
    # The module merges message_start's placeholder output count, so only a finished stream reported its output.
    output_reported = completed and "output_tokens" in usage
    return "input_tokens" in usage, output_reported, response["response_model"], response["service_tier"], completed


def responses_stream_usage(chunk: Any) -> tuple[bool, bool, bool, Optional[str], bool]:
    """What one Responses API stream event says: whether its usage reports the input, output and reasoning counts,
    the model the provider named, and whether it is the provider's last event of a response it finished.
    """
    response, outcome, _ = ai_usage.parse_provider_stream("openai_responses", [chunk])
    usage = response["usage"] or {}
    return (
        "input_tokens" in usage,
        "output_tokens" in usage,
        "reasoning_output_tokens" in usage,
        response["response_model"],
        outcome == "completed",
    )


class _Attempt:
    __slots__ = (
        "start",
        "operation",
        "provider",
        "model",
        "stream",
        "model_id",
        "closed",
        "streaming",
        "llm_provider",
        "input_reported",
        "output_reported",
        "reasoning_reported",
        "response_model",
        "service_tier",
        "completed",
        "operator_ids",
        "provider_ids",
    )

    def __init__(self, kwargs: dict[str, Any]) -> None:
        call_type = kwargs.get("call_type")
        self.start = kwargs.get("api_call_start_time") or datetime.datetime.now()
        self.operation = operation_name(call_type)
        llm_provider = kwargs.get("custom_llm_provider")
        self.llm_provider = llm_provider if isinstance(llm_provider, str) else None
        self.provider = provider_name(llm_provider)
        self.model = kwargs.get("model") if isinstance(kwargs.get("model"), str) else None
        self.stream = bool(kwargs.get("stream"))
        model_info = _model_info(kwargs)
        self.model_id = model_info.get("id") or kwargs.get("model_id")
        self.closed = False
        # What the integration's stream wrappers saw of this attempt's stream: whether it streamed, which counts the
        # provider reported in it, and the model and service tier it named.
        self.streaming = False
        self.input_reported = False
        self.output_reported = False
        self.reasoning_reported = False
        self.response_model: Optional[str] = None
        self.service_tier: Optional[str] = None
        # Whether the stream carried the provider's own last event of a response it finished.
        self.completed = False
        self.operator_ids = _operator_ids(model_info)
        # The provider account, project and key ids of the attempt: the operator's until it is recorded.
        self.provider_ids: dict[str, str] = dict(self.operator_ids)

    @property
    def claude(self) -> bool:
        """Whether the call has an Anthropic shape, whose reasoning and modality details LiteLLM derives itself."""
        return self.llm_provider == "anthropic" or "claude" in (self.model or "").lower()

    def estimated_sides(self) -> tuple[bool, bool]:
        """Whether the input and the output counts are estimates rather than the provider's.

        A streamed side is reported when a chunk or event the integration saw carried it, so a stream it did not see
        is estimated on both sides. A call that was not streamed is estimated when LiteLLM counts its provider's
        usage itself.
        """
        if self.stream:
            return not self.input_reported, not self.output_reported
        local = self.llm_provider in _LOCALLY_COUNTED_PROVIDERS or (
            self.llm_provider == "bedrock" and _bedrock_invoke(self.model)
        )
        return local, local


class _Request:
    __slots__ = (
        "start",
        "created",
        "operation",
        "route",
        "identity",
        "attempts",
        "ended",
    )

    def __init__(self) -> None:
        self.start: Optional[float] = None
        self.created = time.monotonic()
        self.operation: Optional[str] = None
        self.route: Optional[str] = None
        self.identity: dict[str, Any] = {}
        self.attempts: list[_Attempt] = []
        self.ended = False


class UsageMetricsLogger(CustomLogger):  # type: ignore[misc, unused-ignore]
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
        # When each request recorded or evicted lately was forgotten, so that a late hook does not start it again.
        self._forgotten: OrderedDict[str, float] = OrderedDict()
        # LiteLLM's worker threads take this lock too; a fork while one holds it must not leave the child locked.
        self._lock = forksafe.Lock()

    def reset(self) -> None:
        """Forget the requests in flight: in a forked child, they belong to the parent."""
        with self._lock:
            self._requests.clear()
            self._forgotten.clear()

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
        """Forget the oldest requests beyond the bounds. Called with the lock held."""
        now = time.monotonic()
        while self._requests:
            call_id, oldest = next(iter(self._requests.items()))
            if len(self._requests) < _MAX_REQUESTS and oldest.created >= now - _REQUEST_TTL_SECONDS:
                break
            del self._requests[call_id]
            # Its attempts are lost: a late hook must not start it again as a request without them.
            self._remember_forgotten(call_id, now)

    def _remember_forgotten(self, call_id: str, now: float) -> None:
        """Called with the lock held."""
        self._forgotten[call_id] = now
        while self._forgotten:
            oldest = next(iter(self._forgotten.values()))
            if len(self._forgotten) <= _MAX_REQUESTS and oldest >= now - _REQUEST_TTL_SECONDS:
                break
            self._forgotten.popitem(last=False)

    def _was_forgotten(self, call_id: str) -> bool:
        with self._lock:
            return call_id in self._forgotten

    def _forget_if_done(self, call_id: str, request: _Request) -> None:
        if request.ended and all(attempt.closed for attempt in request.attempts):
            with self._lock:
                self._requests.pop(call_id, None)
                self._remember_forgotten(call_id, time.monotonic())

    @staticmethod
    def _open_attempt(request: _Request, kwargs: dict[str, Any]) -> Optional[_Attempt]:
        """The open attempt an event belongs to: the one that started at the event's api_call_start_time,
        else the latest open one.
        """
        start = kwargs.get("api_call_start_time")
        for attempt in reversed(request.attempts):
            if not attempt.closed and start is not None and attempt.start == start:
                return attempt
        return UsageMetricsLogger._latest_open_attempt(request)

    def _close_unreported_attempts(self, request: _Request, keep: Optional[_Attempt]) -> None:
        """Close the attempts of an ended request that no hook reported, other than keep, without recording them.

        On LiteLLM versions without async_post_call_failure_deployment_hook, nothing reports a failed retry of a route
        the integration's wrappers do not see. When it ended is unknown (the router may have waited before the next
        attempt), so it records no duration; the request still counts it among its provider operations.
        """
        # Under the lock _record_attempt claims attempts with, so that each attempt is either recorded or closed here.
        with self._lock:
            for attempt in request.attempts:
                if not attempt.closed and attempt is not keep:
                    attempt.closed = True

    @staticmethod
    def _latest_open_attempt(request: _Request) -> Optional[_Attempt]:
        for attempt in reversed(request.attempts):
            if not attempt.closed:
                return attempt
        return None

    def observe_stream(
        self,
        call_id: Any,
        input_reported: bool,
        output_reported: bool,
        model: Optional[str] = None,
        reasoning_reported: bool = False,
        service_tier: Optional[str] = None,
        completed: bool = False,
    ) -> None:
        """Called by the integration's stream wrappers with what a chunk or event of the latest attempt's stream
        carried: which token counts the provider reported, the model and service tier it named, and whether it was
        the provider's last event of a response it finished.
        """
        request = self._request(call_id, create=False)
        if request is None or not request.attempts:
            return
        attempt = request.attempts[-1]
        attempt.streaming = True
        attempt.input_reported = attempt.input_reported or input_reported
        attempt.output_reported = attempt.output_reported or output_reported
        attempt.reasoning_reported = attempt.reasoning_reported or reasoning_reported
        attempt.completed = attempt.completed or completed
        if model and attempt.response_model is None:
            attempt.response_model = model
        if attempt.service_tier is None:
            attempt.service_tier = valid_service_tier(service_tier)

    def attempt_failed(self, kwargs: dict[str, Any], exception: BaseException) -> None:
        """Called by the integration's wrapper when a chat or text completion attempt raises before streaming.

        Attempts of one request run one after the other, so the failed attempt is the latest open one. On LiteLLM
        versions that call async_post_call_failure_deployment_hook, that hook has already closed it.
        """
        try:
            call_id = kwargs.get("litellm_call_id")
            request = self._request(call_id, create=False)
            if request is None or not isinstance(call_id, str):
                return
            attempt = self._latest_open_attempt(request)
            if attempt is not None:
                self._record_attempt(
                    request, attempt, kwargs, _now_like(attempt.start), error_type(exception) or _UNKNOWN_ERROR, None
                )
                self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: attempt_failed failed", exc_info=True)

    # Deployment attributes

    def _provider_ids(
        self, attempt: _Attempt, kwargs: dict[str, Any], payload: Optional[dict[str, Any]], response: Any
    ) -> dict[str, str]:
        """The provider account, project and key ids of an attempt. The operator's win; the ones found here are
        sent only when their tag is enabled.
        """
        found: dict[str, str] = {}
        enabled = {attribute for tag, attribute in _OPT_IN_PROVIDER_ID_TAGS.items() if tag in self._tags}
        if enabled:
            if attempt.provider is not None:
                ids = ai_usage.provider_id_attributes(
                    attempt.provider, lambda name: _response_header(name, kwargs, payload, response)
                )
                found.update((attribute, value) for attribute, value in ids.items() if attribute in enabled)
            if attempt.provider == "aws.bedrock" and PROVIDER_ACCOUNT_ID in enabled:
                params = kwargs.get("litellm_params") or {}
                account = _bedrock_account(attempt.model, params.get("model_id"), kwargs.get("model_id"))
                if account is not None:
                    found.setdefault(PROVIDER_ACCOUNT_ID, account)
        found.update(attempt.operator_ids or _operator_ids(_model_info(kwargs)))
        return found

    def _deployment_attributes(
        self,
        metadata: dict[str, Any],
        route: Optional[str],
        destination: Any,
        provider_ids: Optional[dict[str, str]] = None,
    ) -> dict[str, str]:
        attributes = dict(self._resource)
        attributes.update(provider_ids or {})
        for tag, (attribute, keys) in _OPT_IN_METADATA_TAGS.items():
            if tag not in self._tags:
                continue
            value = next((metadata[key] for key in keys if isinstance(metadata.get(key), str) and metadata[key]), None)
            if value is not None:
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
        response: Any = None,
    ) -> None:
        # Hooks for one stream can run on different LiteLLM threads; only the one that closes the attempt records it.
        with self._lock:
            if attempt.closed:
                return
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
            original = _original_response(kwargs)
            response_model = original.get("response_model") or attempt.response_model
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
            usage = usage_fields(
                usage_object,
                attempt.operation == "embeddings",
                modality=not attempt.claude,
                # LiteLLM splits a reasoning count out of the output itself on the Anthropic shapes, and on a stream
                # whose provider reported none: the output total stays reported and the part is left out.
                drop_reasoning=attempt.claude or (attempt.stream and not attempt.reasoning_reported),
                usage_format=(
                    "gemini_openai_compat" if attempt.provider == "openai" and _gemini_compat(kwargs) else "auto"
                ),
            )
            dropped_thoughts = None
            if usage.get("output_basis") == "excludes_reasoning":
                # Google's OpenAI-compatible endpoint: the thinking its total counts beyond the completion.
                dropped_thoughts = usage.get("reasoning_output_tokens")
            elif (
                attempt.provider in _GEMINI_PROVIDERS and not attempt.stream and "reasoning_output_tokens" not in usage
            ):
                dropped_thoughts = _gemini_dropped_thoughts(original, usage.get("output_tokens"))
                if dropped_thoughts is not None:
                    # The projector adds the thoughts to the output, which then includes reasoning as the spec requires.
                    usage["reasoning_output_tokens"] = dropped_thoughts
                    usage["output_basis"] = "excludes_reasoning"
            observation.update(usage)
            if usage:
                input_estimated, output_estimated = attempt.estimated_sides()
                if input_estimated:
                    observation["input_token_source"] = "estimated"  # nosec B105: a token count source, not a secret
                if output_estimated:
                    observation["output_token_source"] = "estimated"  # nosec B105: a token count source, not a secret
            cost = payload.get("response_cost")
            # LiteLLM priced the output without the thoughts it left out, so its cost is short of the billed one.
            if dropped_thoughts is not None:
                cost = None
            if isinstance(cost, (int, float)) and not isinstance(cost, bool) and (cost > 0 or _priced_at_zero(kwargs)):
                observation["cost_usd"] = float(cost)
                observation["cost_source"] = "estimated"
            # A stream's assembled response often drops the tier its chunks named.
            tier = service_tier(payload) or attempt.service_tier
            if tier is not None:
                observation["service_tier"] = tier
        metadata = _metadata(kwargs)
        identity = dict(request.identity)
        identity.update({key: metadata[key] for key in _IDENTITY_KEYS if metadata.get(key)})
        attempt.provider_ids = self._provider_ids(attempt, kwargs, payload, response)
        attributes = self._deployment_attributes(
            identity, request.route or metadata.get("original_model_group"), attempt.model_id, attempt.provider_ids
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
        failed: bool = False,
    ) -> None:
        """Record the gateway request. failed marks a request that failed for good, whose cost is what the
        proxy charged for it, zero included.
        """
        with self._lock:
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
        # Retry and fallback counts are left out: the profile counts explicit routing decisions, which LiteLLM does
        # not report reliably, and forbids inferring them from the provider attempts.
        if cache_hit:
            observation["cache_outcomes"] = ["hit"]
            observation["estimated_cost_usd"] = 0
        elif (
            isinstance(cost, (int, float))
            and not isinstance(cost, bool)
            and (failed or cost > 0 or _priced_at_zero(kwargs))
        ):
            observation["estimated_cost_usd"] = float(cost)
        if error:
            observation["error_type"] = error
        # Like the destination, the provider ids are those of the request's last attempt.
        last = request.attempts[-1] if request.attempts else None
        attributes = self._deployment_attributes(
            request.identity,
            request.route,
            last.model_id if last is not None else None,
            last.provider_ids if last is not None else None,
        )
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
                request.identity = _key_identity(user_api_key_dict)
        except Exception:
            log.debug("LiteLLM usage metrics: async_pre_call_hook failed", exc_info=True)
        return None

    def has_request(self, call_id: Any) -> bool:
        request = self._request(call_id, create=False)
        return request is not None and request.start is not None

    def _proxy_request(self, kwargs: dict[str, Any], elapsed: float = 0.0) -> Optional[_Request]:
        """The request a call belongs to. Only calls made by the proxy are recorded.

        Proxies that assign the call id after async_pre_call_hook (LiteLLM 1.6x) have no request yet at their
        first call: it starts there, elapsed seconds ago, with its route and identity from the call's metadata.
        """
        call_id = kwargs.get("litellm_call_id")
        request = self._request(call_id, create=False)
        if request is not None or not isinstance(call_id, str) or self._was_forgotten(call_id):
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

    def _failed_request(self, request_data: dict[str, Any], user_api_key_dict: Any) -> Optional[_Request]:
        """The request a terminal failure ends. One that failed before this logger saw it (rejected by an earlier
        pre-call hook, or, on proxies that assign the call id after async_pre_call_hook, before its first provider
        call) starts when LiteLLM's logging object for it did.
        """
        call_id = request_data.get("litellm_call_id")
        request = self._request(call_id, create=False)
        if request is not None or not isinstance(call_id, str):
            return request
        if self._was_forgotten(call_id):
            return None
        logging_obj = request_data.get("litellm_logging_obj")
        started = getattr(logging_obj, "start_time", None)
        operation = operation_name(getattr(logging_obj, "call_type", None))
        if not isinstance(started, datetime.datetime) or operation is None:
            return None
        request = self._request(call_id)
        if request is not None and request.start is None:
            elapsed = (datetime.datetime.now(started.tzinfo) - started).total_seconds()
            request.start = time.monotonic() - max(elapsed, 0.0)
            request.operation = operation
            route = request_data.get("model")
            request.route = route if isinstance(route, str) else None
            request.identity = _key_identity(user_api_key_dict)
        return request

    def log_pre_api_call(self, model: Any, messages: Any, kwargs: Any) -> None:
        try:
            request = self._proxy_request(kwargs)
            if request is not None:
                attempt = _Attempt(kwargs)
                request.attempts.append(attempt)
                # An attempt with no known operation or provider records nothing, so the backend still derives it.
                if attempt.operation is not None and attempt.provider is not None:
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
                    request,
                    attempt,
                    request_data,
                    _now_like(attempt.start),
                    error_type(exception) or _UNKNOWN_ERROR,
                    None,
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
            error = error_type(error_information.get("error_class"), error_information.get("error_code"))
            attempt = None if cache_hit else self._open_attempt(request, kwargs)
            self._close_unreported_attempts(request, attempt)
            if attempt is not None:
                # The proxy flags a client that left before its own generator finished, even right after the
                # provider's last event: the provider attempt itself ended as it should. The request keeps the error,
                # as nothing tells whether the client read the whole response.
                attempt_error = error
                if attempt.completed and error_information.get("error_class") == _CLIENT_DISCONNECTED:
                    attempt_error = None
                self._record_attempt(request, attempt, kwargs, end_time, attempt_error, payload, response_obj)
            self._record_request(call_id, request, kwargs, error, cache_hit, payload.get("response_cost"))
            self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: async_log_success_event failed", exc_info=True)

    async def async_log_failure_event(self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any) -> None:
        # LiteLLM calls this once per request for non-streamed calls and once per attempt for streamed ones, and
        # possibly after the next attempt started. An attempt that fails before streaming is closed by the wrapper
        # or by async_post_call_failure_deployment_hook, so only an attempt that is still open and matches this
        # event's start time is closed here: a stream that failed before or after its first chunk, or one of a route
        # the wrapper does not see. Whichever hook closes an attempt first records it.
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
            if attempt is None:
                return
            payload = kwargs.get("standard_logging_object") or {}
            error_information = payload.get("error_information") or {}
            error = (
                error_type(
                    error_information.get("error_class") or kwargs.get("exception"),
                    error_information.get("error_code"),
                )
                or _UNKNOWN_ERROR
            )
            self._record_attempt(request, attempt, kwargs, end_time, error, payload)
            self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: async_log_failure_event failed", exc_info=True)

    async def async_post_call_failure_hook(
        self, request_data: Any, original_exception: Any, user_api_key_dict: Any, traceback_str: Any = None
    ) -> Any:
        try:
            call_id = request_data.get("litellm_call_id")
            request = self._failed_request(request_data, user_api_key_dict)
            if request is not None:
                error = error_type(original_exception) or _UNKNOWN_ERROR
                # The last attempt, if no other hook closed it, is the one this failure ends: a stream that broke
                # partway, when this hook comes before LiteLLM's failure event, or a route whose failures fire no
                # failure event at all (native Anthropic Messages streams before 1.104).
                last = self._latest_open_attempt(request)
                self._close_unreported_attempts(request, last)
                if last is not None:
                    self._record_attempt(
                        request, last, request_data, _now_like(last.start), error, _failure_payload(request_data)
                    )
                self._record_request(
                    call_id, request, request_data, error, False, _charged_on_failure(request_data), failed=True
                )
                self._forget_if_done(call_id, request)
        except Exception:
            log.debug("LiteLLM usage metrics: async_post_call_failure_hook failed", exc_info=True)
        return None


def _key_identity(user_api_key_dict: Any) -> dict[str, Any]:
    return {
        "user_api_key_user_email": getattr(user_api_key_dict, "user_email", None),
        "user_api_key_end_user_id": getattr(user_api_key_dict, "end_user_id", None),
        "user_api_key_team_id": getattr(user_api_key_dict, "team_id", None),
        "user_api_key_alias": getattr(user_api_key_dict, "key_alias", None),
    }


def _mark_current_span(profiles: str) -> None:
    """Mark the active provider call's span, and the Router span of its gateway request above it."""
    span = tracer.current_span()
    if span is None or span.name != "litellm.request":
        return
    span._set_attribute(RECORDED_PROFILES_TAG, profiles)
    parent = span._parent
    if parent is not None and parent.name == "litellm.request" and str(parent.resource).startswith("router."):
        parent._set_attribute(RECORDED_PROFILES_TAG, GATEWAY_PROFILES)
