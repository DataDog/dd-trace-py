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
import json
import re
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

# Providers whose usage LiteLLM counts itself on a call that is not streamed: it gets no count from them, or fills in
# a missing one in a way a callback cannot tell apart from a reported count (Ollama, and Bedrock's invoke route).
_LOCALLY_COUNTED_PROVIDERS = frozenset(
    {"replicate", "sagemaker", "langgraph", "predibase", "anthropic_text", "petals", "ollama", "ollama_chat"}
)
# LiteLLM's usage detail fields that carry a provider's own counts by modality. LiteLLM fills text_tokens itself on
# several routes (Bedrock Converse, the text left after a stream's reasoning split), so text is never read from it.
_MODALITIES = (("image_tokens", "image"), ("audio_tokens", "audio"))

# Requests that never end (a lost callback) are forgotten after this long, and at most this many are kept.
_REQUEST_TTL_SECONDS = 15 * 60
_MAX_REQUESTS = 10_000

_IDENTIFIER_INVALID = re.compile(r"[^a-z0-9_.-]")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _identifier(value: Any) -> Optional[str]:
    """A low-cardinality identifier: lower case, [a-z0-9_.-], starting with a letter."""
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
    """RateLimitError -> rate_limit_error."""
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


def usage_fields(usage: Any, embeddings: bool, modality: bool = True) -> dict[str, Any]:
    """The token fields of an observation from LiteLLM's usage object.

    LiteLLM normalizes usage to the Chat Completions shape on every route, including the Anthropic Messages and
    Responses routes: prompt_tokens includes cached input and cache writes, and completion_tokens includes
    reasoning. LiteLLM 1.6x hands over the response's own usage instead, which on those routes has the Responses
    shape (input_tokens and output_tokens, also inclusive) or the native Anthropic Messages shape, whose
    input_tokens leaves cache reads and writes out. A usage object of zeros is LiteLLM's placeholder for unknown
    usage. modality is False when the modality details are LiteLLM's own split rather than the provider's.
    """
    if usage is None:
        return {}
    input_tokens = _count(_get(usage, "prompt_tokens"))
    output_tokens = _count(_get(usage, "completion_tokens"))
    input_basis = "includes_cache"
    prompt_details = _get(usage, "prompt_tokens_details")
    completion_details = _get(usage, "completion_tokens_details")
    if input_tokens is None and output_tokens is None:
        input_tokens = _count(_get(usage, "input_tokens"))
        output_tokens = _count(_get(usage, "output_tokens"))
        prompt_details = _get(usage, "input_tokens_details")
        completion_details = _get(usage, "output_tokens_details")
        if _count(_get(usage, "cache_read_input_tokens")) is not None or (
            _count(_get(usage, "cache_creation_input_tokens")) is not None
        ):
            input_basis = "excludes_cache"
    if not input_tokens and not output_tokens:
        return {}
    fields: dict[str, Any] = {"input_tokens": input_tokens, "input_basis": input_basis}
    if not embeddings:
        fields["output_tokens"] = output_tokens
        fields["output_basis"] = "includes_reasoning"
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
        reasoning = _count(_get(completion_details, "reasoning_tokens"))
        if reasoning is not None:
            fields["reasoning_output_tokens"] = reasoning
    if modality:
        sides = [("input", prompt_details)]
        if not embeddings:
            sides.append(("output", completion_details))
        for side, details in sides:
            # Parts go to the projector as reported, zeros included: it counts what no part covers as unknown, and
            # flags parts that add up to more than the total.
            parts: dict[str, int] = {}
            for key, name in _MODALITIES:
                value = _count(_get(details, key))
                if value is not None:
                    parts[name] = value
            if parts:
                fields[f"{side}_tokens_by_modality"] = parts
    return {key: value for key, value in fields.items() if value is not None}


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


# The characters a scan stops at, a whole JSON string, a number or literal, and what separates a key from its value.
_JSON_DELIMITER = re.compile(r'["{}\[\]]')
_JSON_STRING = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"')
_JSON_SCALAR = re.compile(r"[^,}\]\s]+")
_JSON_COLON = re.compile(r"\s*:\s*")


def _json_value_end(text: str, start: int) -> int:
    """Where the JSON value that starts at start ends, or -1 when it does not."""
    if text.startswith('"', start):
        string = _JSON_STRING.match(text, start)
        return string.end() if string else -1
    if not text.startswith(("{", "["), start):
        scalar = _JSON_SCALAR.match(text, start)
        return scalar.end() if scalar else -1
    depth = 0
    position = start
    while True:
        delimiter = _JSON_DELIMITER.search(text, position)
        if delimiter is None:
            return -1
        if delimiter.group() == '"':
            string = _JSON_STRING.match(text, delimiter.start())
            if string is None:
                return -1
            position = string.end()
            continue
        position = delimiter.end()
        depth += 1 if delimiter.group() in "{[" else -1
        if depth == 0:
            return position


def _json_paths(text: str, paths: frozenset[tuple[str, ...]]) -> dict[tuple[str, ...], str]:
    """The raw JSON of the values at the given key paths of a JSON object, read without decoding the rest: a provider
    response or stream event can hold the whole generated output. One pass, each character read at most once; any
    other value is stepped over whole, and malformed input ends the scan.
    """
    found: dict[tuple[str, ...], str] = {}
    containers = {path[:length] for path in paths for length in range(1, len(path))}
    start = text.find("{")
    if start < 0:
        return found
    # The key path of each object being read, innermost last.
    objects: list[tuple[str, ...]] = [()]
    position = start + 1
    while objects and len(found) < len(paths):
        delimiter = _JSON_DELIMITER.search(text, position)
        if delimiter is None or delimiter.group() not in ('"', "}"):
            break
        if delimiter.group() == "}":
            objects.pop()
            position = delimiter.end()
            continue
        key = _JSON_STRING.match(text, delimiter.start())
        colon = _JSON_COLON.match(text, key.end()) if key else None
        if key is None or colon is None:
            break
        try:
            path = objects[-1] + (json.loads(key.group()),)
        except ValueError:
            break
        if path in containers and text.startswith("{", colon.end()):
            objects.append(path)
            position = colon.end() + 1
            continue
        end = _json_value_end(text, colon.end())
        if end < 0:
            break
        if path in paths:
            found[path] = text[colon.end() : end]
        position = end
    return found


def _json_string(raw: Optional[str]) -> Optional[str]:
    """A non-empty JSON string value from its raw text."""
    if raw is None or not raw.startswith('"'):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, str) and value else None


def _json_object(raw: Optional[str]) -> Optional[dict[str, Any]]:
    """A JSON object from its raw text."""
    if raw is None or not raw.startswith("{"):
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


_MODEL = ("model",)
_EVENT_TYPE = ("type",)
_EVENT_MODEL = ("response", "model")
_EVENT_USAGE = ("response", "usage")
_RESPONSES_EVENT_FIELDS = frozenset({_EVENT_TYPE, _EVENT_MODEL, _EVENT_USAGE})


def _top_level_model(text: str) -> Optional[str]:
    """The top-level "model" of a JSON object; providers put it near the start."""
    return _json_string(_json_paths(text, frozenset({_MODEL})).get(_MODEL))


def _response_model(kwargs: dict[str, Any]) -> Optional[str]:
    """The model the provider's response names. LiteLLM rewrites the response's model to the client's route,
    so read the provider's own response when it is available.
    """
    original = kwargs.get("original_response")
    if isinstance(original, str) and original.startswith("{"):
        return _top_level_model(original)
    if isinstance(original, dict):
        model = original.get("model")
        return model if isinstance(model, str) and model else None
    return None


def _metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    """The request metadata: under litellm_params in callback kwargs, at the top of deployment hook data."""
    metadata = (kwargs.get("litellm_params") or {}).get("metadata") or kwargs.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


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


def _sse_data(chunks: Any) -> Any:
    """The JSON text of each server-sent event in chunks; a chunk can hold several events, or be one bare event."""
    for chunk in chunks:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", errors="replace")
        if not isinstance(chunk, str):
            continue
        for line in chunk.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line.startswith("{"):
                yield line


def _sse_events(chunks: Any, kinds: tuple[str, ...]) -> Any:
    """The JSON events of server-sent event chunks whose text names one of kinds. Only those are decoded: content
    events, which hold the generated output, are skipped. A quote inside a JSON string is escaped, so generated text
    cannot name a kind.
    """
    quoted = tuple(f'"{kind}"' for kind in kinds)
    for line in _sse_data(chunks):
        if not any(kind in line for kind in quoted):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def anthropic_stream_usage(chunks: Any) -> tuple[bool, bool, Optional[str]]:
    """Which sides of a native Anthropic Messages stream the provider reported, and the model it named.

    message_start carries the input count and a placeholder output count; message_delta carries the output
    count. A stream that ends without one leaves LiteLLM to estimate that side.
    """
    input_reported = output_reported = False
    model = None
    for event in _sse_events(chunks, ("message_start", "message_delta")):
        kind = event.get("type")
        if kind == "message_start":
            message = event.get("message") or {}
            input_reported = input_reported or _count(_get(message.get("usage"), "input_tokens")) is not None
            name = message.get("model")
            model = model or (name if isinstance(name, str) and name else None)
        elif kind == "message_delta":
            output_reported = output_reported or _count(_get(event.get("usage"), "output_tokens")) is not None
    return input_reported, output_reported, model


def responses_stream_usage(chunk: Any) -> tuple[bool, bool, bool, Optional[str]]:
    """What one Responses API stream event says: whether its usage reports the input, output and reasoning counts,
    and the model the provider named.
    """
    input_reported = output_reported = reasoning_reported = False
    model = None
    # The final event holds the whole response, generated output included: only these fields are decoded.
    for line in _sse_data([chunk]):
        fields = _json_paths(line, _RESPONSES_EVENT_FIELDS)
        model = model or _json_string(fields.get(_EVENT_MODEL))
        if _json_string(fields.get(_EVENT_TYPE)) in ("response.completed", "response.incomplete", "response.failed"):
            usage = _json_object(fields.get(_EVENT_USAGE))
            input_reported = _count(_get(usage, "input_tokens")) is not None
            output_reported = _count(_get(usage, "output_tokens")) is not None
            reasoning = _get(_get(usage, "output_tokens_details"), "reasoning_tokens")
            reasoning_reported = _count(reasoning) is not None
    return input_reported, output_reported, reasoning_reported, model


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
    )

    def __init__(self, kwargs: dict[str, Any]) -> None:
        metadata = _metadata(kwargs)
        call_type = kwargs.get("call_type")
        self.start = kwargs.get("api_call_start_time") or datetime.datetime.now()
        self.operation = operation_name(call_type)
        llm_provider = kwargs.get("custom_llm_provider")
        self.llm_provider = llm_provider if isinstance(llm_provider, str) else None
        self.provider = provider_name(llm_provider)
        self.model = kwargs.get("model") if isinstance(kwargs.get("model"), str) else None
        self.stream = bool(kwargs.get("stream"))
        self.model_id = (metadata.get("model_info") or {}).get("id") or kwargs.get("model_id")
        self.closed = False
        # What the integration's stream wrappers saw of this attempt's stream: whether it streamed, which counts the
        # provider reported in it, and the model it named.
        self.streaming = False
        self.input_reported = False
        self.output_reported = False
        self.reasoning_reported = False
        self.response_model: Optional[str] = None

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
    ) -> None:
        """Called by the integration's stream wrappers with what a chunk or event of the latest attempt's stream
        carried: which token counts the provider reported, and the model it named.
        """
        request = self._request(call_id, create=False)
        if request is None or not request.attempts:
            return
        attempt = request.attempts[-1]
        attempt.streaming = True
        attempt.input_reported = attempt.input_reported or input_reported
        attempt.output_reported = attempt.output_reported or output_reported
        attempt.reasoning_reported = attempt.reasoning_reported or reasoning_reported
        if model and attempt.response_model is None:
            attempt.response_model = model

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
                    request, attempt, kwargs, _now_like(attempt.start), error_type(exception) or "error", None
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
            response_model = _response_model(kwargs) or attempt.response_model
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
            usage = usage_fields(usage_object, attempt.operation == "embeddings", modality=not attempt.claude)
            # LiteLLM splits a reasoning count out of the output itself on the Anthropic shapes, and on a stream
            # whose provider reported none: the output total stays reported and the part is left out.
            if attempt.claude or (attempt.stream and not attempt.reasoning_reported):
                usage.pop("reasoning_output_tokens", None)
            observation.update(usage)
            if usage:
                input_estimated, output_estimated = attempt.estimated_sides()
                if input_estimated:
                    observation["input_token_source"] = "estimated"  # nosec B105: a token count source, not a secret
                if output_estimated:
                    observation["output_token_source"] = "estimated"  # nosec B105: a token count source, not a secret
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
                    request, attempt, request_data, _now_like(attempt.start), error_type(exception) or "error", None
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
            attempt = None if cache_hit else self._open_attempt(request, kwargs)
            self._close_unreported_attempts(request, attempt)
            if attempt is not None:
                self._record_attempt(request, attempt, kwargs, end_time, error, payload, response_obj)
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
            error = error_type(error_information.get("error_class") or kwargs.get("exception")) or "error"
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
                error = error_type(original_exception) or "error"
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
        "user_api_key_user_id": getattr(user_api_key_dict, "user_id", None),
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
