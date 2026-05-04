"""AI Guard client for security evaluation of agentic AI workflows."""

from copy import deepcopy
import json
from typing import Any
from typing import Literal
from typing import Optional  # noqa:F401
from typing import TypedDict
from typing import Union

from ddtrace import config
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec._trace_utils import _aiguard_manual_keep
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal import telemetry
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.settings.asm import ai_guard_config
from ddtrace.internal.telemetry import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics_namespaces import MetricTagType
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection
from ddtrace.trace import tracer
from ddtrace.version import __version__


logger = ddlogger.get_logger(__name__)

ALLOW = "ALLOW"
DENY = "DENY"
ABORT = "ABORT"
ACTIONS = [ALLOW, DENY, ABORT]


class Function(TypedDict):
    name: str
    arguments: str


class ToolCall(TypedDict):
    id: str
    function: Function


class ImageURL(TypedDict, total=False):
    url: str


class ContentPart(TypedDict, total=False):
    type: str
    text: Optional[str]
    image_url: Optional[ImageURL]


class Message(TypedDict, total=False):
    role: str
    content: Union[str, list[ContentPart]]
    tool_call_id: str
    tool_calls: list[ToolCall]


class Evaluation(TypedDict):
    action: Literal["ALLOW", "DENY", "ABORT"]
    reason: str
    tags: list[str]
    sds: list
    tag_probs: dict[str, float]


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


class AIGuardAbortError(Exception):
    """Exception to abort current execution due to security policy."""

    def __init__(
        self,
        action: str,
        reason: str,
        tags: Optional[list[str]] = None,
        sds: Optional[list] = None,
        tag_probs: Optional[dict[str, float]] = None,
    ):
        self.action = action
        self.reason = reason
        self.tags = tags
        self.sds = sds or []
        self.tag_probs = tag_probs
        super().__init__(f"AIGuardAbortError(action='{action}', reason='{reason}', tags='{tags}')")


class AIGuardClient:
    """HTTP client for communicating with AI Guard security service."""

    def __init__(self, endpoint: str, api_key: str, app_key: str):
        """Initialize AI Guard client.

        Args:
            endpoint: AI Guard service endpoint URL
            api_key: Datadog API key
            app_key: Datadog application key
        """
        self._endpoint = endpoint
        self._headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
            "DD-AI-GUARD-VERSION": __version__,
            "DD-AI-GUARD-SOURCE": "SDK",
            "DD-AI-GUARD-LANGUAGE": "python",
        }
        self._meta = {"service": config.service, "env": config.env}
        self._timeout = ai_guard_config._ai_guard_timeout // 1000

    @staticmethod
    def _add_request_to_telemetry(tags: MetricTagType) -> None:
        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, AI_GUARD.REQUESTS_METRIC, 1, tags)

    @staticmethod
    def _messages_for_meta_struct(messages: list[Message]) -> list[Message]:
        max_messages_length = ai_guard_config._ai_guard_max_messages_length
        if len(messages) > max_messages_length:
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC, AI_GUARD.TRUNCATED_METRIC, 1, (("type", "messages"),)
            )
        messages = messages[-max_messages_length:]

        max_content_size = ai_guard_config._ai_guard_max_content_size
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
                TELEMETRY_NAMESPACE.APPSEC, AI_GUARD.TRUNCATED_METRIC, 1, (("type", "content"),)
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
    def _is_blocking_enabled(options: Optional[Options], remote_enabled: bool) -> bool:
        if not remote_enabled:
            return False
        if not options:
            return True
        return options.get("block", True)

    def evaluate(self, messages: list[Message], options: Optional[Options] = None) -> Evaluation:
        """Evaluate if the list of messages are safe to execute.

        Args:
            messages: list of messages to evaluate
            options: Optional configuration with 'block' parameter. By default, block follows
                the AI Guard response is_blocking_enabled setting; set block=False to force
                non-blocking behavior.

        Returns:
            EvaluationResult containing action and reason

        Raises:
            AIGuardAbortError: If execution should be aborted and block is set to true
            AIGuardClientError: If evaluation request fails
        """
        if not messages or len(messages) == 0:
            raise ValueError("Messages must not be empty")

        with tracer.trace(AI_GUARD.RESOURCE_TYPE) as span:
            try:
                payload = {"data": {"attributes": {"messages": messages, "meta": self._meta}}}
                last = messages[-1]
                tool_name = self._get_tool_name(last, messages)
                if tool_name:
                    span.set_tag(AI_GUARD.TARGET_TAG, "tool")
                    span.set_tag(AI_GUARD.TOOL_NAME_TAG, tool_name)
                else:
                    span.set_tag(AI_GUARD.TARGET_TAG, "prompt")

                meta_struct = {"messages": self._messages_for_meta_struct(messages)}
                span._set_struct_tag(AI_GUARD.STRUCT, meta_struct)

                try:
                    response = self._execute_request(f"{self._endpoint}/evaluate", payload)
                    result = response.get_json() or {}
                except Exception as e:
                    raise AIGuardClientError(message=f"Unexpected error calling AI Guard service: {e}") from e

                if response.status == 200:
                    try:
                        attributes = result["data"]["attributes"]
                        action = attributes["action"]
                        reason = attributes.get("reason", None)
                        tags = attributes.get("tags", [])
                        sds_findings = attributes.get("sds_findings") or []
                        blocking_enabled = attributes.get("is_blocking_enabled", False)
                        tag_probs = attributes.get("tag_probs")
                    except Exception as e:
                        value = json.dumps(result, indent=2)[:500]
                        raise AIGuardClientError(
                            message=f"AI Guard service returned unexpected response format: {value}",
                            status=response.status,
                        ) from e

                    if action not in ACTIONS:
                        raise AIGuardClientError(
                            f"AI Guard service returned unrecognized action: '{action}'. Expected {ACTIONS}",
                            status=response.status,
                        )

                    span.set_tag(AI_GUARD.ACTION_TAG, action)
                    if tags:
                        meta_struct.update({"attack_categories": tags})
                    if reason:
                        span.set_tag(AI_GUARD.REASON_TAG, reason)
                    if sds_findings:
                        meta_struct.update({"sds": sds_findings})
                    if tag_probs is not None:
                        meta_struct.update({"tag_probs": tag_probs})
                else:
                    raise AIGuardClientError(
                        message=f"AI Guard service call failed, status: {response.status}",
                        status=response.status,
                        errors=result["errors"] if "errors" in result else None,
                    )

                should_block = self._is_blocking_enabled(options, blocking_enabled) and action != ALLOW
                self._add_request_to_telemetry(
                    (
                        ("action", action),
                        ("block", "true" if should_block else "false"),
                        ("error", "false"),
                    )
                )
                root_span = core.get_root_span()
                if root_span:
                    _aiguard_manual_keep(root_span)
                    root_span.set_tag(AI_GUARD.EVENT_TAG, "true")
                    # Populate client IP on the service-entry span only when an ai_guard span
                    # is actually created, mirroring the AppSec spec. The candidate IP was
                    # stashed earlier by set_http_meta when DD_AI_GUARD_ENABLED=true.
                    # Discard the key after use so a later evaluate() call can't inherit a
                    # stale IP from an earlier request that shared this context tree.
                    client_ip = core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY)
                    core.discard_item(AI_GUARD.CLIENT_IP_CORE_KEY)
                    entry_span = root_span._service_entry_span
                    if client_ip:
                        entry_span._set_attribute(http.CLIENT_IP, client_ip)
                        entry_span._set_attribute("network.client.ip", client_ip)
                    # Copy anomaly-detection attributes from the service-entry span onto the
                    # ai_guard span with the `ai_guard.` prefix, so intake processing has them
                    # even when the entry span arrives in a later trace chunk.
                    for tag_name in AI_GUARD.ANOMALY_DETECTION_TAGS:
                        tag_value = entry_span.get_tag(tag_name)
                        if tag_value is not None:
                            span.set_tag(f"{AI_GUARD.TAG}.{tag_name}", tag_value)
                if should_block:
                    span.set_tag(AI_GUARD.BLOCKED_TAG, "true")
                    raise AIGuardAbortError(
                        action=action,
                        reason=reason,
                        tags=tags,
                        sds=sds_findings,
                        tag_probs=tag_probs,
                    )

                return Evaluation(action=action, reason=reason, tags=tags, sds=sds_findings, tag_probs=tag_probs)

            except AIGuardAbortError:
                raise

            except Exception:
                self._add_request_to_telemetry((("error", "true"),))
                logger.debug("AI Guard evaluation failed for messages: %s", messages, exc_info=True)
                raise

    def _execute_request(self, url: str, payload: Any) -> Response:
        try:
            conn = get_connection(url, self._timeout)
            json_body = json.dumps(payload, ensure_ascii=True, skipkeys=True, default=str)
            conn.request("POST", url, json_body, self._headers)
            resp = conn.getresponse()
            return Response.from_http_response(resp)
        finally:
            conn.close()


def new_ai_guard_client(
    endpoint: Optional[str] = None,
) -> AIGuardClient:
    api_key = config._dd_api_key
    app_key = config._dd_app_key
    if not api_key or not app_key:
        raise ValueError("Authentication credentials required: provide DD_API_KEY and DD_APP_KEY")

    if not endpoint:
        endpoint = ai_guard_config._ai_guard_endpoint
    if not endpoint:
        site = f"app.{config._dd_site}" if config._dd_site.count(".") == 1 else config._dd_site
        endpoint = f"https://{site}/api/v2/ai-guard"

    return AIGuardClient(endpoint, api_key, app_key)
