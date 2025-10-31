"""AI Guard client for security evaluation of agentic AI workflows."""
import json
from typing import Any
from typing import List
from typing import Literal
from typing import Optional  # noqa:F401
from typing import TypedDict

import ddtrace
from ddtrace import config
from ddtrace import tracer as ddtracer
from ddtrace._trace.tracer import Tracer
from ddtrace.appsec._constants import AI_GUARD
from ddtrace.internal import telemetry
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.telemetry import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics_namespaces import MetricTagType
from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection
from ddtrace.settings.asm import ai_guard_config


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


class Message(TypedDict, total=False):
    role: str
    content: str
    tool_call_id: str
    tool_calls: List[ToolCall]


class Evaluation(TypedDict):
    action: Literal["ALLOW", "DENY", "ABORT"]
    reason: str


class Options(TypedDict, total=False):
    block: bool


class Error(TypedDict, total=False):
    status: str
    title: str
    code: str
    detail: str


class AIGuardClientError(Exception):
    """Exception for AI Guard client errors."""

    def __init__(self, message: Optional[str], status: int = 0, errors: Optional[List[Error]] = None):
        self.status = status
        self.errors = errors or []
        super().__init__(message)


class AIGuardAbortError(Exception):
    """Exception to abort current execution due to security policy."""

    def __init__(self, action: str, reason: str):
        self.action = action
        self.reason = reason
        super().__init__(f"AIGuardAbortError(action='{action}', reason='{reason}')")


class AIGuardClient:
    """HTTP client for communicating with AI Guard security service."""

    def __init__(self, endpoint: str, api_key: str, app_key: str, tracer: Tracer):
        """Initialize AI Guard client.

        Args:
            endpoint: AI Guard service endpoint URL
            api_key: Datadog API key
            app_key: Datadog application key
            tracer: Datadog tracer instance
        """

        self._tracer = tracer
        self._endpoint = endpoint
        self._headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
            "DD-AI-GUARD-VERSION": ddtrace.__version__,
            "DD-AI-GUARD-SOURCE": "SDK",
            "DD-AI-GUARD-LANGUAGE": "python",
        }
        self._meta = {"service": config.service, "env": config.env}
        self._timeout = ai_guard_config._ai_guard_timeout // 1000

    @staticmethod
    def _add_request_to_telemetry(tags: MetricTagType) -> None:
        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, AI_GUARD.REQUESTS_METRIC, 1, tags)

    @staticmethod
    def _messages_for_meta_struct(messages: List[Message]) -> List[Message]:
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
            content = message.get("content", "")
            if len(content) > max_content_size:
                truncated = message.copy()
                truncated["content"] = content[:max_content_size]
                content_truncated = True
                return truncated
            return message

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
    def _get_tool_name(message: Message, messages: List[Message]) -> Optional[str]:
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
        if not remote_enabled or not options:
            return False
        return options.get("block", False)

    def evaluate(self, messages: List[Message], options: Optional[Options] = None) -> Evaluation:
        """Evaluate if the list of messages are safe to execute.

        Args:
            messages: List of messages to evaluate
            options: Optional configuration with 'block' parameter (defaults to False)

        Returns:
            EvaluationResult containing action and reason

        Raises:
            AIGuardAbortError: If execution should be aborted and block is set to true
            AIGuardClientError: If evaluation request fails
        """
        if not messages or len(messages) == 0:
            raise ValueError("Messages must not be empty")

        with self._tracer.trace(AI_GUARD.RESOURCE_TYPE) as span:
            try:
                payload = {"data": {"attributes": {"messages": messages, "meta": self._meta}}}
                last = messages[-1]
                tool_name = self._get_tool_name(last, messages)
                if tool_name:
                    span.set_tag(AI_GUARD.TARGET_TAG, "tool")
                    span.set_tag(AI_GUARD.TOOL_NAME_TAG, tool_name)
                else:
                    span.set_tag(AI_GUARD.TARGET_TAG, "prompt")
                span.set_struct_tag(AI_GUARD.STRUCT, {"messages": self._messages_for_meta_struct(messages)})

                try:
                    response = self._execute_request(f"{self._endpoint}/evaluate", payload)
                    result = response.get_json()
                except Exception as e:
                    raise AIGuardClientError(message=f"Unexpected error calling AI Guard service: {e}") from e

                if response.status == 200:
                    try:
                        attributes = result["data"]["attributes"]
                        action = attributes["action"]
                        reason = attributes.get("reason", None)
                        blocking_enabled = attributes.get("is_blocking_enabled", False)
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
                    if reason:
                        span.set_tag(AI_GUARD.REASON_TAG, reason)
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

                if should_block:
                    span.set_tag(AI_GUARD.BLOCKED_TAG, "true")
                    raise AIGuardAbortError(action=action, reason=reason)

                return Evaluation(action=action, reason=reason)

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
    tracer: Tracer = ddtracer,
) -> AIGuardClient:
    api_key = config._dd_api_key
    app_key = config._dd_app_key
    if not api_key or not app_key:
        raise ValueError("Authentication credentials required: provide DD_API_KEY and DD_APP_KEY")

    if not endpoint:
        site = f"app.{config._dd_site}" if config._dd_site.count(".") == 1 else config._dd_site
        endpoint = f"https://{site}/api/v2/ai-guard"

    return AIGuardClient(endpoint, api_key, app_key, tracer)
