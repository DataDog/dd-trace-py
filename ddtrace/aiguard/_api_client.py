"""AI Guard client for security evaluation of agentic AI workflows."""

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
import json
import re
from typing import Any
from typing import Optional
from typing import Protocol
from typing import TypedDict
from urllib.parse import urlparse

from ddtrace import config
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._redaction import redact_messages
from ddtrace.aiguard._trace_utils import _aiguard_manual_keep
from ddtrace.aiguard._types import MCP
from ddtrace.aiguard._types import ContentPart
from ddtrace.aiguard._types import Evaluation
from ddtrace.aiguard._types import Function
from ddtrace.aiguard._types import ImageURL
from ddtrace.aiguard._types import MCPServer
from ddtrace.aiguard._types import Message
from ddtrace.aiguard._types import Policy
from ddtrace.aiguard._types import PolicyEvaluation
from ddtrace.aiguard._types import ResourceTarget
from ddtrace.aiguard._types import ToolCall
from ddtrace.aiguard.middleware import _MIDDLEWARES
from ddtrace.aiguard.middleware import AIGuardContext
from ddtrace.aiguard.middleware import AIGuardRequest
from ddtrace.aiguard.middleware import EvaluationDirection
from ddtrace.aiguard.middleware import EvaluationTarget
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.constants import ENV_KEY
from ddtrace.constants import MANUAL_DROP_KEY
from ddtrace.constants import MANUAL_KEEP_KEY
from ddtrace.constants import SERVICE_KEY
from ddtrace.constants import SERVICE_VERSION_KEY
from ddtrace.constants import VERSION_KEY
from ddtrace.contrib.internal.trace_utils_base import set_client_address_tags
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal import agent
from ddtrace.internal import core
from ddtrace.internal import forksafe
from ddtrace.internal import span_bus
from ddtrace.internal import telemetry
from ddtrace.internal._exceptions import DDBlockException
from ddtrace.internal.agent import AGENT_CHECK_INTERVAL
from ddtrace.internal.http import HTTPConnection
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.native import ConnectionFailedError
from ddtrace.internal.native import HttpClientError
from ddtrace.internal.native import HttpIoError
from ddtrace.internal.native import InvalidConfigError
from ddtrace.internal.native import TimedOutError
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.telemetry import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.constants import MetricTagType
from ddtrace.internal.threads import Thread
from ddtrace.internal.threads import original_Event
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.time import HourGlass
from ddtrace.version import __version__


__all__ = ["ToolCall", "Message", "Function", "ContentPart", "ImageURL", "Evaluation", "MCP", "MCPServer"]


logger = ddlogger.get_logger(__name__)

ALLOW = "ALLOW"
DENY = "DENY"
ABORT = "ABORT"
ACTIONS = [ALLOW, DENY, ABORT]
_RESERVED_TAG_PREFIX = AI_GUARD.TAG + "."
# Backend extra tags never override what the tracer computes itself, nor span control keys
# (sampling, service, version, env).
_RESERVED_EXTRA_TAGS = frozenset(
    (
        AI_GUARD.ACTION_TAG,
        AI_GUARD.REASON_TAG,
        AI_GUARD.TARGET_TAG,
        AI_GUARD.DIRECTION_TAG,
        AI_GUARD.SOURCE_TAG,
        AI_GUARD.INTEGRATION_TAG,
        AI_GUARD.TOOL_NAME_TAG,
        AI_GUARD.BLOCKED_TAG,
        AI_GUARD.REDACTED_TAG,
        AI_GUARD.EVENT_TAG,
        *(f"{AI_GUARD.TAG}.{tag}" for tag in AI_GUARD.ANOMALY_DETECTION_TAGS),
        MANUAL_KEEP_KEY,
        MANUAL_DROP_KEY,
        SERVICE_KEY,
        SERVICE_VERSION_KEY,
        VERSION_KEY,
        ENV_KEY,
        _SPAN_MEASURED_KEY,
    )
)
_RESERVED_EXTRA_STRUCT_KEYS = frozenset(("messages", "mcp_servers", "attack_categories", "sds", "tag_probs"))
# Root span tags that anomaly detection reads under their OTel names when OTel semantics are enabled.
_OTEL_ANOMALY_SOURCE_TAGS = {
    http.USER_AGENT: otel_http.USER_AGENT_ORIGINAL,
    http.CLIENT_IP: otel_http.CLIENT_ADDRESS,
    net.CLIENT_IP: otel_http.NETWORK_PEER_ADDRESS,
}
_SKIPPED_REASON = "skipped by middleware"


def _call_hook(hook: Any, context: AIGuardContext) -> Any:
    """Call a customer middleware hook; a failing hook must never break the evaluation."""
    try:
        return hook(context)
    except Exception:
        logger.debug("AI Guard middleware hook %r raised, ignoring it", hook, exc_info=True)
        return None


def _get_direction(message: Message) -> EvaluationDirection:
    """An assistant message is what the LLM produced; anything else is fed into it."""
    return EvaluationDirection.OUTPUT if message.get("role") == "assistant" else EvaluationDirection.INPUT


class Options(TypedDict, total=False):
    """Optional evaluation behavior.

    Attributes:
        block: Controls whether non-ALLOW decisions raise ``AIGuardAbortError``. Defaults to
            following the AI Guard response ``is_blocking_enabled`` setting when omitted.
    """

    block: bool


class Error(TypedDict, total=False):
    status: str
    title: str
    code: str
    detail: str


class AIGuardClientError(Exception):
    """Exception for AI Guard client errors."""

    def __init__(self, message: Optional[str], status: int = 0, errors: Optional[list[Error]] = None):
        self.status = status
        self.errors = errors or []
        super().__init__(message)


class _NoAgentRouteError(AIGuardClientError):
    """No transport is available: no credentials, and no Agent advertising the AI Guard route."""


class AIGuardAbortError(DDBlockException):
    """Exception to abort current execution due to security policy.

    Inherits from ``DDBlockException`` (which is ``BaseException``-derived) so
    that a generic ``except Exception:`` in user code does not accidentally
    swallow an AI Guard block decision.
    """

    def __init__(
        self,
        action: str,
        reason: str,
        tags: Optional[list[str]] = None,
        sds: Optional[list[Any]] = None,
        tag_probs: Optional[dict[str, float]] = None,
    ):
        self.action = action
        self.reason = reason
        self.tags = tags
        self.sds = sds or []
        self.tag_probs = tag_probs
        # Do not include the messages attribute as it can have
        # sensitive data and be large
        super().__init__(f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')")


# Transport failures the native HTTP client distinguishes. Ordered most specific first so a
# subclass is never shadowed by its base.
_TRANSPORT_ERROR_TYPES: tuple[tuple[type[BaseException], str], ...] = (
    (ConnectionFailedError, AI_GUARD.ERROR_CONNECTION),
    (TimedOutError, AI_GUARD.ERROR_TIMEOUT),
    (InvalidConfigError, AI_GUARD.ERROR_INVALID_CONFIG),
    (HttpIoError, AI_GUARD.ERROR_NETWORK),
)


def _classify_transport_error(exc: BaseException) -> str:
    """Map a failure from the evaluate request onto an error metric type.

    Splitting these apart is what separates "never reached the service" from "reached it and was
    rejected"; client_error remains the catch-all for an unrecognised transport failure, and
    anything that is not a transport failure at all is reported as an internal error.
    """
    for error_cls, error_type in _TRANSPORT_ERROR_TYPES:
        if isinstance(exc, error_cls):
            return error_type
    return AI_GUARD.ERROR_CLIENT if isinstance(exc, HttpClientError) else AI_GUARD.ERROR_INTERNAL


def _status_tag(status: Optional[int]) -> str:
    """Clamp a response status to the declared allowlist, keeping the tag bounded."""
    return str(status) if status in AI_GUARD.STATUSES else AI_GUARD.STATUS_OTHER


# Error types that are transport outcomes; any other classification is tagged as other.
_AGENT_ERROR_TYPES = frozenset((AI_GUARD.ERROR_TIMEOUT, AI_GUARD.ERROR_CONNECTION, AI_GUARD.ERROR_NETWORK))


def _agent_status_tag(status: Optional[int]) -> str:
    """Report the status of an Agent request, including success, clamped to a bounded allowlist."""
    if status is None:
        return AI_GUARD.STATUS_NONE
    return str(status) if status in AI_GUARD.AGENT_STATUSES else AI_GUARD.AGENT_STATUS_OTHER


def _agent_error_type(error_type: Optional[str]) -> str:
    if error_type is None:
        return AI_GUARD.AGENT_ERROR_NONE
    return error_type if error_type in _AGENT_ERROR_TYPES else AI_GUARD.AGENT_ERROR_OTHER


def _target_result(body: Any) -> Optional[dict[str, Any]]:
    """The result for the first target of a policy evaluation, or None unless exactly one result matches it.

    A result matches if its target_index is the int 0, or if it has no target_index and is the only result.
    """
    try:
        results = body["data"]["attributes"]["results"]
    except Exception:
        return None
    if not isinstance(results, list):
        return None
    if len(results) == 1 and isinstance(results[0], dict) and "target_index" not in results[0]:
        return results[0]
    # With several results, any result without target_index makes the answer ambiguous, in any order.
    if any(isinstance(result, dict) and "target_index" not in result for result in results):
        return None
    # bool is a subclass of int, so the exact type check also rejects False.
    matches = [
        result
        for result in results
        if isinstance(result, dict) and type(result["target_index"]) is int and result["target_index"] == 0
    ]
    return matches[0] if len(matches) == 1 else None


def _policy_field(policy: Any, key: str) -> Optional[str]:
    """A field of the result policy as a string. The service can send it as a string or as an integer."""
    value = policy.get(key) if isinstance(policy, dict) else None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    # An empty string identifies nothing, so it gives no tag, as an empty reason does.
    return str(value) if value != "" else None


# What replaces an endpoint anywhere it would otherwise be reported.
_REDACTED = "<endpoint>"

# A URL in any shape an endpoint override reaches the transport as: absolute, scheme-relative, or
# the bare authority left when the scheme is missing. The last character cannot be punctuation, so
# a URL ending a sentence does not swallow what closes it.
_URL_RE = re.compile(r"(?:[A-Za-z][A-Za-z0-9+.\-]*:)?//[^\s'\"]*[^\s'\"(),.;]")


def _scrub_urls(text: str) -> str:
    """Replace every URL in text with a placeholder.

    A credential can sit in the userinfo, in a path segment or in a query parameter, so no part of
    the endpoint is kept.
    """
    return _URL_RE.sub(_REDACTED, text)


@dataclass(frozen=True)
class _TransportResponse:
    response: Response
    agent_error_code: Optional[str] = None
    route_gone: bool = False


class _AIGuardTransport(Protocol):
    name: str
    endpoint: str

    def send(self, url: str, payload: Any) -> _TransportResponse: ...

    def describe_bad_status(self, result: _TransportResponse) -> str: ...


def _request_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "DD-AI-GUARD-VERSION": __version__,
        "DD-AI-GUARD-SOURCE": "SDK",
        "DD-AI-GUARD-LANGUAGE": "python",
    }


def _post_json(
    base_url: str, target: str, payload: Any, headers: dict[str, str], timeout: int, response_headers: tuple[str, ...]
) -> tuple[Response, dict[str, Optional[str]]]:
    conn = HTTPConnection(base_url, timeout=timeout)
    try:
        json_body = json.dumps(payload, ensure_ascii=True, skipkeys=True, default=str)
        conn.request("POST", target, json_body, headers)
        resp = conn.getresponse()
        return Response.from_http_response(resp), {name: resp.getheader(name) for name in response_headers}
    finally:
        conn.close()


class _DirectTransport:
    """Calls the AI Guard API with the service's own credentials."""

    name: str = AI_GUARD.TRANSPORT_DIRECT

    def __init__(self, endpoint: str, api_key: Optional[str], app_key: Optional[str]):
        self.endpoint = endpoint
        self._timeout = aiguard_config._ai_guard_timeout // 1000
        self._headers = _request_headers()
        # Each key is sent on its own, so a caller passing only one keeps the header it used to send.
        if api_key:
            self._headers["DD-API-KEY"] = api_key
        if app_key:
            self._headers["DD-APPLICATION-KEY"] = app_key

    def send(self, url: str, payload: Any) -> _TransportResponse:
        parsed = urlparse(url)
        # Userinfo is dropped rather than forwarded: AI Guard authenticates with headers, and the
        # transport logs and quotes the base URL it is given.
        base_url = f"{parsed.scheme}://{parsed.netloc.rpartition('@')[2]}"
        target = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        response, _ = _post_json(base_url, target, payload, self._headers, self._timeout, ())
        return _TransportResponse(response)

    def describe_bad_status(self, result: _TransportResponse) -> str:
        return f"AI Guard service call failed, status: {result.response.status}"


class _AgentTransport:
    """Sends evaluations through the local Agent proxy route; the Agent attaches the credentials."""

    name: str = AI_GUARD.TRANSPORT_AGENT
    endpoint: str = f"{AI_GUARD.AGENT_PROXY_ENDPOINT}{AI_GUARD.AGENT_PROXY_API_PATH}"

    def __init__(self, base_url: Optional[str] = None):
        """base_url pins the Agent instead of using the process-wide discovery."""
        self._base_url = base_url
        self._timeout = aiguard_config._ai_guard_timeout // 1000
        self._headers = _request_headers()

    def resolve_base_url(self) -> str:
        if self._base_url is not None:
            return self._base_url
        base_url, reason = _agent_route.resolve()
        if base_url is None:
            raise _NoAgentRouteError(
                message=(
                    "No way to reach AI Guard: provide DD_API_KEY and DD_APP_KEY, "
                    f"or run an Agent configured with an application key ({reason})"
                )
            )
        return base_url

    def send(self, url: str, payload: Any) -> _TransportResponse:
        # The native client keeps the path of an HTTP base URL, and for a Unix socket the base
        # names the socket, so the target is the request path alone.
        response, headers = _post_json(
            self.resolve_base_url(),
            url,
            payload,
            self._headers,
            self._timeout,
            (AI_GUARD.AGENT_ERROR_HEADER, AI_GUARD.AGENT_VERSION_HEADER),
        )
        # Only the Agent route sets its version header, so a 404 without it means the route is gone.
        # A pinned Agent does not own the shared discovery state.
        route_gone = (
            response.status == 404 and headers[AI_GUARD.AGENT_VERSION_HEADER] is None and self._base_url is None
        )
        if route_gone:
            _agent_route.invalidate("the Agent no longer advertises the AI Guard proxy route")
        return _TransportResponse(response, headers[AI_GUARD.AGENT_ERROR_HEADER], route_gone)

    def describe_bad_status(self, result: _TransportResponse) -> str:
        status = result.response.status
        if status in (401, 403):
            return (
                f"AI Guard rejected the Agent's credentials, status: {status}. The Agent's API key "
                "and application key must be valid and allowed to evaluate AI Guard requests."
            )
        if result.route_gone:
            return "The Agent no longer serves the AI Guard proxy route, status: 404"
        return f"AI Guard service call failed, status: {status}"


def _copy_request_tags(span: Any, root_span: Any) -> None:
    """Set the client IPs on the root span, and copy its anomaly-detection tags to the ai_guard span."""
    # Populate client IPs on the service-entry span only when an ai_guard span
    # is actually created, mirroring the AppSec spec. The candidate IPs were
    # stashed earlier by set_http_meta when DD_AI_GUARD_ENABLED=true.
    # Discard the key after use so a later evaluate() call can't inherit a
    # stale IP from an earlier request that shared this context tree.
    client_ips = core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY)
    core.discard_item(AI_GUARD.CLIENT_IP_CORE_KEY)
    if client_ips:
        client_ip, peer_ip = client_ips
        set_client_address_tags(root_span, client_ip, peer_ip)
    # Copy anomaly-detection attributes from the root span onto the
    # ai_guard span with the `ai_guard.` prefix, so intake processing has them
    # even when the root span arrives in a later trace chunk.
    source_tags = _OTEL_ANOMALY_SOURCE_TAGS if config._otel_trace_semantics_enabled else {}
    for tag_name in AI_GUARD.ANOMALY_DETECTION_TAGS:
        tag_value = root_span.get_tag(source_tags.get(tag_name, tag_name))
        if tag_value is None:
            # Inferred proxy roots keep the Datadog attribute names under OTel semantics.
            tag_value = root_span.get_tag(tag_name)
        if tag_value is not None:
            span.set_tag(f"{AI_GUARD.TAG}.{tag_name}", tag_value)


class AIGuardClient:
    """HTTP client for communicating with AI Guard security service."""

    def __init__(
        self,
        endpoint: str = "",
        api_key: Optional[str] = None,
        app_key: Optional[str] = None,
        transport: Optional[_AIGuardTransport] = None,
    ):
        """Initialize AI Guard client.

        Args:
            endpoint: AI Guard service endpoint URL
            api_key: Datadog API key
            app_key: Datadog application key
            transport: how evaluations reach AI Guard; defaults to calling endpoint directly
                with the given keys
        """
        self._transport = transport or _DirectTransport(endpoint, api_key, app_key)
        self._endpoint = self._transport.endpoint
        self._meta = {"service": config.service, "env": config.env}

    @staticmethod
    def _call_path_tags(source: str, integration: str) -> tuple[tuple[str, str], ...]:
        """Tags identifying the call path that reached the evaluation, required on every metric.

        Clamped to the declared values: the spec pins integration to none for direct SDK calls,
        and evaluate is public API, so an off-spec value must not reach telemetry as a new series.
        """
        if source not in AI_GUARD.SOURCES:
            source = AI_GUARD.SOURCE_SDK
        if source != AI_GUARD.SOURCE_AUTO or integration not in AI_GUARD.INTEGRATIONS:
            integration = AI_GUARD.INTEGRATION_NONE
        return (("source", source), ("integration", integration))

    @staticmethod
    def _add_request_to_telemetry(tags: MetricTagType) -> None:
        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.AI_GUARD, AI_GUARD.REQUESTS_METRIC, 1, tags)

    def _add_transport_to_telemetry(self) -> None:
        telemetry.telemetry_writer.add_gauge_metric(
            TELEMETRY_NAMESPACE.AI_GUARD,
            AI_GUARD.TRANSPORT_METRIC,
            1,
            ((AI_GUARD.TRANSPORT_TAG, self._transport.name),),
        )

    def _add_agent_request_to_telemetry(
        self, status: Optional[int], error_type: Optional[str], agent_error: Optional[str] = None
    ) -> None:
        if self._transport.name != AI_GUARD.TRANSPORT_AGENT:
            return
        tags = (
            (AI_GUARD.STATUS_CODE_TAG, _agent_status_tag(status)),
            (AI_GUARD.ERROR_TYPE_TAG, _agent_error_type(error_type)),
            (AI_GUARD.AGENT_ERROR_TAG, agent_error or AI_GUARD.AGENT_ERROR_NONE),
        )
        telemetry.telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.AI_GUARD, AI_GUARD.AGENT_REQUESTS_METRIC, 1, tags
        )

    @staticmethod
    def _add_error_to_telemetry(error_type: str, tags: tuple[tuple[str, str], ...], count: int = 1) -> None:
        telemetry.telemetry_writer.add_count_metric(
            TELEMETRY_NAMESPACE.AI_GUARD, AI_GUARD.ERROR_METRIC, count, (("type", error_type),) + tags
        )

    @staticmethod
    def _messages_for_meta_struct(
        messages: list[Message], call_path_tags: tuple[tuple[str, str], ...]
    ) -> list[Message]:
        max_messages_length = aiguard_config._ai_guard_max_messages_length
        if len(messages) > max_messages_length:
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.AI_GUARD,
                AI_GUARD.TRUNCATED_METRIC,
                1,
                (("type", "messages"),) + call_path_tags,
            )
        messages = messages[-max_messages_length:] if max_messages_length else []

        max_content_size = aiguard_config._ai_guard_max_content_size
        content_truncated = False

        def truncate_message(message: Message) -> Message:
            nonlocal content_truncated
            # ensure the message cannot be modified before serialization
            new_message = deepcopy(message)
            content = new_message.get("content", "")
            if isinstance(content, str):
                if len(content) > max_content_size:
                    new_message["content"] = content[:max_content_size]
                    content_truncated = True
            elif isinstance(content, list):
                # Handle list[ContentPart] - truncate text in content parts
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        text = part.get("text", "")
                        if isinstance(text, str) and len(text) > max_content_size:
                            part["text"] = text[:max_content_size]
                            content_truncated = True
            return new_message

        result = [truncate_message(message) for message in messages]
        if content_truncated:
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.AI_GUARD,
                AI_GUARD.TRUNCATED_METRIC,
                1,
                (("type", "content"),) + call_path_tags,
            )
        return result

    @staticmethod
    def _has_tool_calls(message: Message) -> bool:
        tool_calls = message.get("tool_calls")
        return bool(tool_calls and len(tool_calls) > 0)

    @staticmethod
    def _has_tool_call_id(message: Message) -> bool:
        tool_call_id = message.get("tool_call_id")
        return bool(tool_call_id and len(tool_call_id) > 0)

    @staticmethod
    def _get_tool_name(message: Message, messages: list[Message]) -> Optional[str]:
        # assistant message with tool calls
        if AIGuardClient._has_tool_calls(message):
            tool_calls = message.get("tool_calls", [])
            names = [
                tool_call["function"]["name"]
                for tool_call in tool_calls
                if "function" in tool_call and "name" in tool_call["function"]
            ]
            return ",".join(name for name in names if name)

        # assistant message with tool output (search linked tool call in reverse order)
        if AIGuardClient._has_tool_call_id(message):
            target_id = message.get("tool_call_id")
            if not target_id:
                return None
            for msg in reversed(messages):
                tool_calls = msg.get("tool_calls", [])
                for tool_call in tool_calls:
                    if (
                        "id" in tool_call
                        and tool_call["id"] == target_id
                        and "function" in tool_call
                        and "name" in tool_call["function"]
                    ):
                        return tool_call["function"]["name"]

        return None

    @staticmethod
    def _extra_entries(field: str, entries: object) -> Iterator[tuple[str, Any]]:
        """Yield the (key, value) pairs of a backend list of {"key": ..., "value": ...} objects.

        Keys are dotted, which is why the backend sends a list instead of a JSON object.
        """
        if entries is None:
            return
        if not isinstance(entries, list):
            logger.debug("Ignoring AI Guard %s of type %s", field, type(entries).__name__)
            return
        for entry in entries:
            key = entry.get("key") if isinstance(entry, dict) else None
            if isinstance(key, str) and key:
                yield key, entry.get("value")
            else:
                logger.debug("Ignoring malformed AI Guard %s entry", field)

    @staticmethod
    def _apply_extra_tags(
        span: SpanData, meta_struct: dict[str, Any], extra_tags: object, extra_metastruct: object
    ) -> None:
        """Copy backend-provided extra_tags to the span and extra_metastruct to the ai_guard meta_struct.

        Best effort: a malformed payload is skipped and must never change the evaluation outcome.
        """
        try:
            for key, value in AIGuardClient._extra_entries("extra_tags", extra_tags):
                if key in _RESERVED_EXTRA_TAGS:
                    continue
                if isinstance(value, str):
                    span._set_attribute(key, value)
                else:
                    logger.debug("Ignoring AI Guard extra tag %s of type %s", key, type(value).__name__)

            for key, value in AIGuardClient._extra_entries("extra_metastruct", extra_metastruct):
                if value is not None and key not in _RESERVED_EXTRA_STRUCT_KEYS:
                    meta_struct[key] = value
        except Exception:
            logger.debug("Could not apply AI Guard extra_tags", exc_info=True)

    @staticmethod
    def _is_blocking_enabled(options: Optional[Options], remote_enabled: bool) -> bool:
        if not remote_enabled:
            return False
        if not options:
            return True
        return options.get("block", True)

    def evaluate(
        self,
        messages: list[Message],
        options: Optional[Options] = None,
        source: str = AI_GUARD.SOURCE_SDK,
        integration: str = AI_GUARD.INTEGRATION_NONE,
        tags: Optional[dict[str, Any]] = None,
        mcp_servers: Optional[list[MCPServer]] = None,
    ) -> Evaluation:
        """Evaluate if the list of messages are safe to execute.

        Args:
            messages: list of messages to evaluate
            options: Optional configuration with 'block' parameter. By default, block follows
                the AI Guard response is_blocking_enabled setting; set block=False to force
                non-blocking behavior.
            source: Which call path triggered the evaluation, reported as the source telemetry
                tag. Defaults to sdk (a direct customer call); our AI package
                auto-instrumentation passes auto.
            integration: Name of the auto-instrumented AI package, reported as the integration
                telemetry tag. Only meaningful when source is auto; otherwise reported as none.
            tags: Optional custom tags set on the AI Guard span, e.g. to filter evaluations in
                queries. Keys under the reserved ai_guard. prefix are ignored.
            mcp_servers: MCP servers referenced by the mcp.server_id of the tool calls in
                messages. Sent along with the messages; omitted when empty.

        Returns:
            EvaluationResult containing action and reason

        Raises:
            AIGuardAbortError: If execution should be aborted and block is set to true
            AIGuardClientError: If evaluation request fails or no global tracer has been initialized
        """
        if not messages or len(messages) == 0:
            raise ValueError("Messages must not be empty")

        # The tracer comes from the core registry rather than ddtrace.trace so AI Guard doesn't depend on the
        # tracing product. The verdict is reported on the ai_guard span, so evaluating without a tracer would
        # silently drop it.
        tracer = core.root.get_item("tracer")
        if tracer is None:
            raise AIGuardClientError("AI Guard requires the global tracer, but no tracer has been initialized")

        # Classifies the error metric when a raise below escapes. Transport and response paths
        # each set their own type, so anything still holding the default failed inside our own
        # code, which is what internal_error means; client_error is reserved for a transport
        # failure the native client reports but we do not recognise.
        error_type: str = AI_GUARD.ERROR_INTERNAL
        error_status: Optional[int] = None
        call_path_tags = self._call_path_tags(source, integration)
        (_, clamped_source), (_, clamped_integration) = call_path_tags

        last = messages[-1]
        # role "tool" counts on its own: converters emit legacy function results with an empty tool_call_id.
        target = (
            EvaluationTarget.TOOL
            if self._has_tool_calls(last) or self._has_tool_call_id(last) or last.get("role") == "tool"
            else EvaluationTarget.PROMPT
        )
        context = None
        if _MIDDLEWARES:
            request = AIGuardRequest(clamped_source, clamped_integration, _get_direction(last), target, messages)
            context = AIGuardContext(request, span_bus.get_root_span())
            for middleware in _MIDDLEWARES:
                # Only an explicit False skips: a None, a truthy value or a failing hook evaluates.
                if _call_hook(middleware.should_evaluate, context) is False:
                    self._add_request_to_telemetry((("skipped", "true"),) + call_path_tags)
                    return Evaluation(
                        action="ALLOW",
                        reason=_SKIPPED_REASON,
                        tags=[],
                        sds=[],
                        tag_probs=None,  # type: ignore[typeddict-item]
                        messages=messages,
                    )

        with tracer.trace(AI_GUARD.RESOURCE_TYPE) as span:
            if tags:
                for key, value in tags.items():
                    # Some ai_guard.* tags are only set conditionally below, so a caller value for
                    # one of them would survive and misreport the verdict.
                    if not key.startswith(_RESERVED_TAG_PREFIX):
                        span.set_tag(key, value)
            try:
                request_attributes: dict[str, Any] = {"messages": messages, "meta": self._meta}
                if mcp_servers:
                    request_attributes["mcp_servers"] = mcp_servers
                payload = {"data": {"attributes": request_attributes}}
                span.set_tag(AI_GUARD.TARGET_TAG, target.value)
                if target is EvaluationTarget.TOOL:
                    # The tool name is best effort: a tool result whose call is not in the
                    # conversation is still a tool target, it just has no name to report.
                    tool_name = self._get_tool_name(last, messages)
                    if tool_name:
                        span.set_tag(AI_GUARD.TOOL_NAME_TAG, tool_name)
                span.set_tag(AI_GUARD.DIRECTION_TAG, _get_direction(last).value)
                span.set_tag(AI_GUARD.SOURCE_TAG, clamped_source)
                span.set_tag(AI_GUARD.INTEGRATION_TAG, clamped_integration)
                # After the core tags, so middlewares see a fully tagged span.
                if context is not None:
                    context.ai_guard_span = span
                    for middleware in _MIDDLEWARES:
                        _call_hook(middleware.pre_evaluate, context)

                self._add_transport_to_telemetry()
                transport_error: Optional[AIGuardClientError] = None
                try:
                    sent = self._execute_request(f"{self._endpoint}/evaluate", payload)
                except _NoAgentRouteError as e:
                    # Nothing was sent, so there is no Agent request to count.
                    error_type = AI_GUARD.ERROR_CONNECTION
                    transport_error = e
                except Exception as e:
                    error_type = _classify_transport_error(e)
                    transport_error = AIGuardClientError(message=self._describe_transport_error(e))
                    self._add_agent_request_to_telemetry(None, error_type)
                else:
                    self._add_agent_request_to_telemetry(sent.response.status, None, sent.agent_error_code)
                if transport_error is not None:
                    # Raised outside the handler on purpose: that leaves __cause__ and __context__
                    # empty, so nothing can render the transport failure or its endpoint again.
                    raise transport_error
                response = sent.response

                try:
                    result = response.get_json() or {}
                except Exception as e:
                    # A body we cannot decode is a response problem, not a transport one, unless
                    # the status code already explains the failure.
                    if response.status == 200:
                        error_type = AI_GUARD.ERROR_BAD_RESPONSE
                    else:
                        error_type = AI_GUARD.ERROR_BAD_STATUS
                        error_status = response.status
                    raise AIGuardClientError(
                        message=f"AI Guard service returned an undecodable response body: {e}",
                        status=response.status,
                    ) from e

                if response.status == 200:
                    try:
                        attributes = result["data"]["attributes"]
                        action = attributes["action"]
                        reason = attributes.get("reason", None)
                        attack_categories = attributes.get("tags", [])
                        # Reported verbatim: location offsets are computed on the redacted string, so
                        # they only line up when the redacted messages are what ends up reported.
                        sds_findings = attributes.get("sds_findings") or []
                        blocking_enabled = attributes.get("is_blocking_enabled", False)
                        tag_probs = attributes.get("tag_probs")
                        # Presence of the field is the signal to redact; sds_findings are detection
                        # metadata only and never drive redaction.
                        redaction_replacements = attributes.get("redaction_replacements")
                        extra_tags = attributes.get("extra_tags")
                        extra_metastruct = attributes.get("extra_metastruct")
                    except Exception as e:
                        error_type = AI_GUARD.ERROR_BAD_RESPONSE
                        value = json.dumps(result, indent=2)[:500]
                        raise AIGuardClientError(
                            message=f"AI Guard service returned unexpected response format: {value}",
                            status=response.status,
                        ) from e

                    if action not in ACTIONS:
                        error_type = AI_GUARD.ERROR_BAD_RESPONSE
                        raise AIGuardClientError(
                            f"AI Guard service returned unrecognized action: '{action}'. Expected {ACTIONS}",
                            status=response.status,
                        )

                    span.set_tag(AI_GUARD.ACTION_TAG, action)
                    redacted_messages = messages
                    redaction_enabled = aiguard_config._ai_guard_redaction_enabled
                    # Not truthiness: a present but malformed payload has redaction errors to report.
                    if redaction_enabled and redaction_replacements is not None:
                        redacted_messages, redaction_errors = redact_messages(messages, redaction_replacements)
                        # A replacement we cannot apply is reported and then forgotten: redaction is
                        # best effort and must never fail the evaluation it rode in on.
                        if redaction_errors:
                            self._add_error_to_telemetry(AI_GUARD.ERROR_REDACTION, call_path_tags, redaction_errors)
                    # redact_messages returns the very same list when nothing was applied.
                    redacted = redacted_messages is not messages
                    if redaction_enabled:
                        span.set_tag(AI_GUARD.REDACTED_TAG, "true" if redacted else "false")

                    meta_struct: dict[str, Any] = {
                        "messages": self._messages_for_meta_struct(redacted_messages, call_path_tags)
                    }
                    # Resolves the mcp.server_id of the tool calls in messages to the server it names.
                    if mcp_servers:
                        meta_struct["mcp_servers"] = deepcopy(mcp_servers)
                    span._set_struct_tag(AI_GUARD.STRUCT, meta_struct)

                    if attack_categories:
                        meta_struct.update({"attack_categories": attack_categories})
                    if reason:
                        span.set_tag(AI_GUARD.REASON_TAG, reason)
                    if sds_findings:
                        meta_struct.update({"sds": sds_findings})
                    if tag_probs is not None:
                        meta_struct.update({"tag_probs": tag_probs})
                    self._apply_extra_tags(span, meta_struct, extra_tags, extra_metastruct)
                else:
                    error_type = AI_GUARD.ERROR_BAD_STATUS
                    error_status = response.status
                    raise AIGuardClientError(
                        message=self._transport.describe_bad_status(sent),
                        status=response.status,
                        errors=result["errors"] if "errors" in result else None,
                    )

                should_block = self._is_blocking_enabled(options, blocking_enabled) and action != ALLOW
                telemetry_tags = [
                    ("action", action),
                    ("block", "true" if should_block else "false"),
                    ("error", "false"),
                ]
                # No tag at all when redaction is off, so absent is distinguishable from "nothing redacted".
                if redaction_enabled:
                    telemetry_tags.append(("redacted", "true" if redacted else "false"))
                telemetry_tags.append(("skipped", "false"))
                telemetry_tags.extend(call_path_tags)
                self._add_request_to_telemetry(tuple(telemetry_tags))
                root_span = span_bus.get_root_span()
                if root_span:
                    _aiguard_manual_keep(root_span)
                    root_span.set_tag(AI_GUARD.EVENT_TAG, "true")
                    _copy_request_tags(span, root_span)
                evaluation = Evaluation(
                    action=action,
                    reason=reason,
                    tags=attack_categories,
                    sds=sds_findings,
                    tag_probs=tag_probs,
                    messages=redacted_messages,
                )
                if context is not None:
                    context.result = evaluation
                if should_block:
                    span.set_tag(AI_GUARD.BLOCKED_TAG, "true")
                    raise AIGuardAbortError(
                        action=action,
                        reason=reason,
                        tags=attack_categories,
                        sds=sds_findings,
                        tag_probs=tag_probs,
                    )

                return evaluation

            except AIGuardAbortError as e:
                if context is not None:
                    context.error = e
                raise

            except Exception as e:
                if context is not None:
                    context.error = e
                self._add_request_to_telemetry((("error", "true"), ("skipped", "false")) + call_path_tags)
                error_tags = call_path_tags
                # Only bad_status has a status to report, and an absent tag stays distinguishable
                # from a status we deliberately clamped away.
                if error_status is not None:
                    error_tags += (("http_status", _status_tag(error_status)),)
                self._add_error_to_telemetry(error_type, error_tags)
                # Log the classification and only the size of the conversation: the messages may
                # carry sensitive data that redaction would have removed, and this runs before any
                # redaction decision is known.
                logger.debug(
                    "AI Guard evaluation failed (%s) for %d messages", error_type, len(messages), exc_info=True
                )
                raise

            finally:
                # Inside the span so post hooks can still tag it, whatever the outcome.
                if context is not None:
                    for middleware in _MIDDLEWARES:
                        _call_hook(middleware.post_evaluate, context)

    def _evaluate_policies(
        self,
        target: ResourceTarget,
        options: Optional[Options] = None,
        source: str = AI_GUARD.SOURCE_SDK,
        integration: str = AI_GUARD.INTEGRATION_NONE,
    ) -> PolicyEvaluation:
        """Evaluate one target, for example an MCP server connection, against the AI Guard policies.

        Raises AIGuardAbortError if the decision must block, and AIGuardClientError if the evaluation fails.
        The caller builds the target from sanitized metadata: the method sends and reports it as it is.
        """
        tracer = core.root.get_item("tracer")
        if tracer is None:
            raise AIGuardClientError("AI Guard requires the global tracer, but no tracer has been initialized")

        # As in evaluate: a failure that no step below classifies is a failure in our own code.
        error_type: str = AI_GUARD.ERROR_INTERNAL
        error_status: Optional[int] = None
        call_path_tags = self._call_path_tags(source, integration)
        (_, clamped_source), (_, clamped_integration) = call_path_tags

        with tracer.trace(AI_GUARD.RESOURCE_TYPE) as span:
            try:
                span.set_tag(AI_GUARD.TARGET_TAG, target["type"])
                span.set_tag(AI_GUARD.SOURCE_TAG, clamped_source)
                span.set_tag(AI_GUARD.INTEGRATION_TAG, clamped_integration)
                attributes = {
                    "profile": AI_GUARD.POLICY_PROFILE_CUSTOM_AGENTS,
                    "targets": [target],
                    "meta": self._meta,
                }

                transport_error: Optional[AIGuardClientError] = None
                try:
                    sent = self._execute_request(
                        f"{self._endpoint}/policies/evaluate", {"data": {"attributes": attributes}}
                    )
                except _NoAgentRouteError as e:
                    error_type = AI_GUARD.ERROR_CONNECTION
                    transport_error = e
                except Exception as e:
                    error_type = _classify_transport_error(e)
                    transport_error = AIGuardClientError(message=self._describe_transport_error(e))
                if transport_error is not None:
                    # As in evaluate, the raise is outside the handler, so that no exception chain
                    # includes the transport failure.
                    raise transport_error
                response = sent.response

                try:
                    body = response.get_json() or {}
                except Exception as e:
                    if response.status == 200:
                        error_type = AI_GUARD.ERROR_BAD_RESPONSE
                    else:
                        error_type = AI_GUARD.ERROR_BAD_STATUS
                        error_status = response.status
                    raise AIGuardClientError(
                        message=f"AI Guard service returned an undecodable response body: {e}",
                        status=response.status,
                    ) from e

                if response.status != 200:
                    error_type = AI_GUARD.ERROR_BAD_STATUS
                    error_status = response.status
                    raise AIGuardClientError(
                        message=self._transport.describe_bad_status(sent),
                        status=response.status,
                        errors=body.get("errors") if isinstance(body, dict) else None,
                    )

                result = _target_result(body)
                if result is None:
                    error_type = AI_GUARD.ERROR_BAD_RESPONSE
                    value = json.dumps(body, indent=2)[:500]
                    raise AIGuardClientError(
                        message=f"AI Guard service returned no single result for the target: {value}",
                        status=response.status,
                    )
                action = result.get("action")
                if action not in ACTIONS:
                    error_type = AI_GUARD.ERROR_BAD_RESPONSE
                    raise AIGuardClientError(
                        f"AI Guard service returned unrecognized action: '{action}'. Expected {ACTIONS}",
                        status=response.status,
                    )
                blocking_enabled = result.get("is_blocking_enabled", False)
                if not isinstance(blocking_enabled, bool):
                    # A string such as "false" is truthy, so a non-boolean flag must not decide the block.
                    error_type = AI_GUARD.ERROR_BAD_RESPONSE
                    raise AIGuardClientError(
                        "AI Guard service returned a non-boolean is_blocking_enabled value", status=response.status
                    )

                span.set_tag(AI_GUARD.ACTION_TAG, action)
                reason = result.get("reason")
                if not isinstance(reason, str):
                    reason = ""
                if reason:
                    span.set_tag(AI_GUARD.REASON_TAG, reason)
                evaluation = PolicyEvaluation(action=action, reason=reason)
                policy = Policy()
                result_policy = result.get("policy")
                policy_id = _policy_field(result_policy, "id")
                policy_version = _policy_field(result_policy, "version")
                if policy_id is not None:
                    span.set_tag(AI_GUARD.POLICY_ID_TAG, policy_id)
                    policy["id"] = policy_id
                if policy_version is not None:
                    span.set_tag(AI_GUARD.POLICY_VERSION_TAG, policy_version)
                    policy["version"] = policy_version
                if policy:
                    evaluation["policy"] = policy
                # A copy, so that a later change to the target cannot change what the span reports.
                span._set_struct_tag(AI_GUARD.STRUCT, {"targets": [deepcopy(target)]})

                should_block = self._is_blocking_enabled(options, blocking_enabled) and action != ALLOW
                self._add_request_to_telemetry(
                    (
                        ("action", action),
                        ("block", "true" if should_block else "false"),
                        ("error", "false"),
                        ("skipped", "false"),
                    )
                    + call_path_tags
                )
                root_span = span_bus.get_root_span()
                if root_span:
                    _aiguard_manual_keep(root_span)
                    root_span.set_tag(AI_GUARD.EVENT_TAG, "true")
                    _copy_request_tags(span, root_span)
                if should_block:
                    span.set_tag(AI_GUARD.BLOCKED_TAG, "true")
                    raise AIGuardAbortError(action=action, reason=reason)
                return evaluation

            # AIGuardAbortError is a BaseException, so a block is not reported as a failure.
            except Exception:
                self._add_request_to_telemetry((("error", "true"), ("skipped", "false")) + call_path_tags)
                error_tags = call_path_tags
                if error_status is not None:
                    error_tags += (("http_status", _status_tag(error_status)),)
                self._add_error_to_telemetry(error_type, error_tags)
                logger.debug("AI Guard policy evaluation failed (%s)", error_type, exc_info=True)
                raise

    def _scrub(self, text: str) -> str:
        """Remove the configured endpoint from text this client is about to report."""
        # The literal value goes first: an endpoint too malformed to match a URL still reaches the
        # transport, which quotes it back in its own messages.
        if self._endpoint:
            text = text.replace(self._endpoint, _REDACTED)
        return _scrub_urls(text)

    def _describe_transport_error(self, exc: BaseException) -> str:
        """Describe a transport failure as the only text this client reports about it.

        The exception is never chained, so this string is the whole report: an endpoint is quoted
        not just by the message but by notes, by group members and by every link a traceback walks,
        and scrubbing each of those is chasing a graph that keeps growing.
        """
        header = f"Unexpected error calling AI Guard service ({type(exc).__name__})"
        try:
            return f"{header}: {self._scrub(str(exc))}"
        except Exception:
            # No exc_info: rendering this failure's context would quote the message we could not read.
            logger.debug("Could not render AI Guard transport error message (%s)", type(exc).__name__)
            return header

    def _execute_request(self, url: str, payload: Any) -> _TransportResponse:
        return self._transport.send(url, payload)


class _AgentRouteDiscovery:
    """Process-wide discovery of the Agent AI Guard proxy route.

    The route can appear after startup, so absence is re-checked at most once per
    AGENT_CHECK_INTERVAL; transient failures are retried by the next evaluation.
    """

    def __init__(self) -> None:
        self._lock = forksafe.Lock()
        self._throttle = HourGlass(duration=AGENT_CHECK_INTERVAL)
        self._base_url: Optional[str] = None
        self._generation = 0
        self._pending: Optional[original_Event] = None
        self._started = False
        self._reason = "the Agent has not been checked for the AI Guard proxy route yet"

    def _probe(self, generation: int, completed: original_Event) -> None:
        """Check the Agent outside the lock; a stale generation discards the result."""
        unsupported = False
        base_url, reason = None, "the Agent /info endpoint could not be read"
        try:
            info = agent.info(raise_on_404=True)
            if info is not None:
                endpoints = {endpoint.lstrip("/") for endpoint in (info.get("endpoints") or [])}
                unsupported = AI_GUARD.AGENT_PROXY_ENDPOINT.lstrip("/") not in endpoints
                reason = "the Agent does not advertise the AI Guard proxy route" if unsupported else ""
                base_url = agent_config.trace_agent_url.rstrip("/") if not unsupported else None
        except agent.AgentInfoNotFoundError:
            unsupported = True
            reason = "the Agent does not expose the /info endpoint"
        except Exception:
            logger.debug("Could not read the Agent /info endpoint", exc_info=True)
            unsupported = False
            base_url, reason = None, "the Agent /info endpoint could not be read"

        with self._lock:
            if generation == self._generation:
                self._base_url = base_url
                self._reason = reason
                self._pending = None
                if unsupported:
                    self._throttle = HourGlass(duration=AGENT_CHECK_INTERVAL)
                    self._throttle.turn()
        completed.set()

    def resolve(self) -> tuple[Optional[str], str]:
        """Return the Agent connection base, or None and why there is none."""
        with self._lock:
            if self._base_url is not None:
                return self._base_url, ""
            completed = self._pending
            if completed is None and self._throttle.trickling():
                return None, self._reason
            probe = completed is None
            if completed is None:
                completed = self._pending = forksafe.Event()
                self._reason = "the Agent AI Guard proxy discovery is still in progress"
            generation = self._generation

        if probe:
            self._probe(generation, completed)
        else:
            # Share the in-flight probe, bounded in case its thread is delayed.
            completed.wait(agent_config.trace_agent_timeout_seconds)

        with self._lock:
            return self._base_url, self._reason

    def reset(self) -> None:
        """Forget everything discovered so far, including the throttle. For tests."""
        with self._lock:
            self._base_url = None
            self._generation += 1
            self._throttle = HourGlass(duration=AGENT_CHECK_INTERVAL)
            completed, self._pending = self._pending, None
            self._started = False
            self._reason = "the Agent has not been checked for the AI Guard proxy route yet"
        if completed is not None:
            completed.set()

    def invalidate(self, reason: str) -> None:
        """Forget the route and go back to discovery, as after the Agent was replaced."""
        with self._lock:
            self._base_url = None
            self._generation += 1
            completed, self._pending = self._pending, None
            self._started = False
            self._reason = reason
            # A fresh glass: turn() on a running one flips it back and skips the delay.
            self._throttle = HourGlass(duration=AGENT_CHECK_INTERVAL)
            self._throttle.turn()
        if completed is not None:
            completed.set()

    def start(self) -> None:
        """Run the first check in the background, so a slow Agent cannot delay startup."""
        with self._lock:
            if self._started:
                return
            self._started = True
            generation = self._generation

        def discover() -> None:
            self.resolve()

        try:
            thread = Thread(target=discover, name="ddtrace.aiguard.discovery")
            thread.start()
        except Exception:
            with self._lock:
                if generation == self._generation:
                    self._started = False
            raise


_agent_route = _AgentRouteDiscovery()
forksafe.register(_agent_route.reset)


def new_ai_guard_client(
    endpoint: Optional[str] = None,
) -> AIGuardClient:
    api_key = config._dd_api_key
    app_key = config._dd_app_key

    # Credentials on the service win over the Agent route.
    if api_key and app_key:
        if not endpoint:
            endpoint = aiguard_config._ai_guard_endpoint
        if not endpoint:
            site = f"app.{config._dd_site}" if config._dd_site.count(".") == 1 else config._dd_site
            endpoint = f"https://{site}/api/v2/ai-guard"
        return AIGuardClient(endpoint, api_key, app_key)

    # Without both keys, the Agent proxies AI Guard with its own credentials. An endpoint override
    # names a direct endpoint, so it does not apply. The route is resolved per evaluation, so an
    # Agent that is not ready yet does not prevent setup.
    try:
        _agent_route.start()
    except (OSError, RuntimeError):
        # Evaluations can still discover the Agent synchronously.
        logger.debug("Could not start AI Guard Agent discovery in the background", exc_info=True)
    return AIGuardClient(transport=_AgentTransport())
