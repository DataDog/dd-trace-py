"""AI Guard client for security evaluation of agentic AI workflows."""
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional  # noqa:F401
from typing import Text
from typing import TypedDict
from typing import Union

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


class _PromptOptional(TypedDict, total=False):
    output: str


class Prompt(_PromptOptional):
    role: str
    content: str


class _ToolCallOptional(TypedDict, total=False):
    output: str


class ToolCall(_ToolCallOptional):
    tool_name: str
    tool_args: Dict[Union[Text, bytes], Any]


Evaluation = Union[Prompt, ToolCall]


class AIGuardClientError(Exception):
    """Exception for AI Guard client errors."""

    pass


class AIGuardAbortError(Exception):
    """Exception to abort current execution due to security policy."""

    pass


class AIGuardWorkflow:
    """Manages conversation history and evaluates tool calls against AI Guard policies."""

    def __init__(self, client: "AIGuardClient"):
        self._client = client
        self._history: List[Evaluation] = []

    def add_system_prompt(self, content: str, output: Optional[str] = None) -> "AIGuardWorkflow":
        """Add a system prompt to the history of the workflow"""
        return self.add_prompt("system", content, output)

    def add_user_prompt(self, content: str, output: Optional[str] = None) -> "AIGuardWorkflow":
        """Add a user prompt to the history of the workflow"""
        return self.add_prompt("user", content, output)

    def add_assistant_prompt(self, content: str, output: Optional[str] = None) -> "AIGuardWorkflow":
        """Add an assistant prompt to the history of the workflow"""
        return self.add_prompt("assistant", content, output)

    def add_prompt(self, role: str, content: str, output: Optional[str] = None) -> "AIGuardWorkflow":
        """Add a prompt to the history of the workflow"""
        current = Prompt(role=role, content=content)
        if output is not None:
            current["output"] = output
        self._history.append(current)
        return self

    def add_tool(
        self, tool_name: str, tool_args: Dict[Union[Text, bytes], Any], output: Optional[str] = None
    ) -> "AIGuardWorkflow":
        """Add a tool execution to the history of the workflow"""
        current = ToolCall(tool_name=tool_name, tool_args=tool_args)
        if output is not None:
            current["output"] = output
        self._history.append(current)
        return self

    def evaluate_tool(
        self,
        tool_name: str,
        tool_args: Dict[Union[Text, bytes], Any],
        output: Optional[str] = None,
        tags: Optional[Dict[Union[Text, bytes], Any]] = None,
    ) -> bool:
        return self._client.evaluate_tool(tool_name, tool_args, output=output, history=self._history, tags=tags)

    def evaluate_prompt(self, role: str, content: str, tags: Optional[Dict[Union[Text, bytes], Any]] = None) -> bool:
        return self._client.evaluate_prompt(role, content, history=self._history, tags=tags)


class AIGuardClient:
    """HTTP client for communicating with AI Guard security service."""

    def __init__(self, endpoint: str, timeout: float, api_key: str, app_key: str, tracer: Tracer):
        """Initialize AI Guard client.

        Args:
            endpoint: AI Guard service endpoint URL
            timeout: Request timeout in seconds
            api_key: Datadog API key
            app_key: Datadog application key
            tracer: Datadog tracer instance
        """

        self._tracer = tracer
        self._endpoint = endpoint
        self._timeout = timeout
        self._headers = {
            "Content-Type": "application/json",
            "DD-API-KEY": api_key,
            "DD-APPLICATION-KEY": app_key,
        }

    def new_workflow(self) -> AIGuardWorkflow:
        """Create a new workflow instance for managing conversation history."""
        return AIGuardWorkflow(client=self)

    @staticmethod
    def _add_request_to_telemetry(tags: MetricTagType) -> None:
        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, AI_GUARD.REQUESTS_METRIC, 1, tags)

    def evaluate_tool(
        self,
        tool_name: str,
        tool_args: Dict[Union[Text, bytes], Any],
        output: Optional[str] = None,
        history: Optional[List[Evaluation]] = None,
        tags: Optional[Dict[Union[Text, bytes], Any]] = None,
    ) -> bool:
        """Evaluate if a tool call is safe to execute.

        Args:
            tool_name: Name of the tool being called
            tool_args: Arguments passed to the tool
            output: Output of the tool call
            history: History of previous tool calls or prompts
            tags: Tags to set on the created span

        Returns:
            True if tool execution is allowed, False if denied

        Raises:
            AIGuardAbortError: If execution should be aborted
            AIGuardClientError: If evaluation request fails
        """
        if history is None:
            history = []

        if tags is None:
            tags = {}
        tags[AI_GUARD.TARGET_TAG] = "tool"
        tags[AI_GUARD.TOOL_NAME_TAG] = tool_name

        tool_call = ToolCall(tool_name=tool_name, tool_args=tool_args)
        if output is not None:
            tool_call["output"] = output

        return self._evaluate(tool_call, history, tags)

    def evaluate_prompt(
        self,
        role: str,
        content: str,
        output: Optional[str] = None,
        history: Optional[List[Evaluation]] = None,
        tags: Optional[Dict[Union[Text, bytes], Any]] = None,
    ) -> bool:
        """Evaluate if a prompt is safe to execute.

        Args:
            role: Role of the prompt author
            content: The prompt content
            output: Output of the prompt
            history: History of previous tool calls or prompts
            tags: Tags to set on the created span

        Returns:
            True if prompt execution is allowed, False if denied

        Raises:
            AIGuardAbortError: If execution should be aborted
            AIGuardClientError: If evaluation request fails
        """
        if history is None:
            history = []

        if tags is None:
            tags = {}
        tags[AI_GUARD.TARGET_TAG] = "prompt"

        prompt = Prompt(role=role, content=content)
        if output is not None:
            prompt["output"] = output

        return self._evaluate(prompt, history, tags)

    def _set_ai_guard_tags(
        self, span, action: str, reason: Optional[str], blocked: bool, current: Evaluation, history: List[Evaluation]
    ):
        span.set_tag(AI_GUARD.ACTION_TAG, action)
        if reason:
            span.set_tag(AI_GUARD.REASON_TAG, reason)

        if blocked:
            span.set_tag(AI_GUARD.BLOCKED_TAG, "true")

        if history:
            max_history_length = ai_guard_config._ai_guard_max_history_length
            if len(history) > max_history_length:
                history = history[-max_history_length:]
                telemetry.telemetry_writer.add_count_metric(
                    TELEMETRY_NAMESPACE.APPSEC, AI_GUARD.TRUNCATED_METRIC, 1, (("type", "history"),)
                )

        content_truncated = False
        max_content_size = ai_guard_config._ai_guard_max_content_size

        def truncate_content(evaluation: Evaluation) -> Evaluation:
            nonlocal content_truncated

            if "content" in evaluation and len(str(evaluation["content"])) > max_content_size:  # type: ignore[typeddict-item]
                truncated = evaluation.copy()
                truncated["content"] = str(truncated["content"])[:max_content_size]  # type: ignore[typeddict-item, typeddict-unknown-key]
                content_truncated = True
                return truncated

            if "output" in evaluation and len(str(evaluation["output"])) > max_content_size:
                truncated = evaluation.copy()
                truncated["output"] = str(truncated["output"])[:max_content_size]
                content_truncated = True
                return truncated

            return evaluation

        span.set_struct_tag(
            AI_GUARD.STRUCT,
            {
                "history": [truncate_content(e) for e in history],
                "current": truncate_content(current),
            },
        )

        if content_truncated:
            telemetry.telemetry_writer.add_count_metric(
                TELEMETRY_NAMESPACE.APPSEC, AI_GUARD.TRUNCATED_METRIC, 1, (("type", "content"),)
            )

    def _evaluate(self, current: Evaluation, history: List[Evaluation], tags: Dict[Union[Text, bytes], Any]) -> bool:
        """Send evaluation request to AI Guard service."""
        with self._tracer.trace(AI_GUARD.RESOURCE_TYPE) as span:
            if tags is not None:
                span.set_tags(tags)
            try:
                if history is None:
                    history = []

                attributes: dict[str, Any] = {"history": history, "current": current}
                if config.service and config.env:
                    attributes["meta"] = {"service": config.service, "env": config.env}
                payload = {"data": {"attributes": attributes}}

                try:
                    response = self._execute_request(f"{self._endpoint.rstrip('/')}/evaluate", payload)
                    if response.status != 200:
                        raise AIGuardClientError(f"AI Guard service call failed, status {response.status}")
                    result = response.get_json()
                except AIGuardClientError:
                    raise
                except Exception as e:
                    raise AIGuardClientError("Unexpected error calling AI Guard service") from e

                try:
                    attributes = result["data"]["attributes"]
                    action = attributes["action"]
                    reason = attributes.get("reason", None)
                    blocking_enabled = attributes.get("is_blocking_enabled", False)
                except Exception as e:
                    value = json.dumps(result, indent=2)[:500]
                    raise AIGuardClientError(f"AI Guard service returned unexpected response format: {value}") from e

                if action not in ACTIONS:
                    raise AIGuardClientError(
                        f"AI Guard service returned unrecognized action: '{action}'. Expected {ACTIONS}"
                    )

                should_block = blocking_enabled and action != ALLOW

                self._set_ai_guard_tags(span, action, reason, should_block, current, history)

                self._add_request_to_telemetry(
                    (
                        ("action", action),
                        ("error", "false"),
                    )
                )
                if not should_block:
                    return True
                elif action == DENY:
                    return False
                else:
                    raise AIGuardAbortError()

            except AIGuardAbortError:
                raise

            except:
                self._add_request_to_telemetry((("error", "true"),))
                logger.debug("AI Guard evaluation failed for event: %s", current, exc_info=True)
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
    timeout: Optional[float] = None,
    api_key: Optional[str] = None,
    app_key: Optional[str] = None,
    tracer: Tracer = ddtracer,
) -> AIGuardClient:
    endpoint = endpoint if endpoint is not None else ai_guard_config._ai_guard_endpoint
    if not endpoint:
        raise ValueError("AI Guard endpoint URL is required: provide DD_AI_GUARD_ENDPOINT")

    api_key = api_key if api_key is not None else config._dd_api_key
    app_key = app_key if app_key is not None else ai_guard_config._dd_app_key
    if not api_key or not app_key:
        raise ValueError("Authentication credentials required: provide DD_API_KEY and DD_APP_KEY")

    timeout = timeout if timeout is not None else 30

    return AIGuardClient(endpoint, timeout, api_key, app_key, tracer)
