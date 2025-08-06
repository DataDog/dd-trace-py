"""AI Guard client for security evaluation of agentic AI workflows."""
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Text
from typing import Union

from ddtrace.internal.utils.http import Response
from ddtrace.internal.utils.http import get_connection


try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict
from typing_extensions import NotRequired

from ddtrace import config
from ddtrace import tracer as ddtracer
from ddtrace._trace.tracer import Tracer
from ddtrace.internal import telemetry
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.telemetry import TELEMETRY_NAMESPACE
from ddtrace.internal.telemetry.metrics_namespaces import MetricTagType
from ddtrace.settings.asm import ai_guard_config


logger = ddlogger.get_logger(__name__)

ALLOW = "ALLOW"
DENY = "DENY"
ABORT = "ABORT"
ACTIONS = [ALLOW, DENY, ABORT]


class Prompt(TypedDict):
    role: str
    content: str
    output: NotRequired[str]


class ToolCall(TypedDict):
    tool_name: str
    tool_args: Dict[Union[Text, bytes], Any]
    output: NotRequired[str]


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

    def add_system_prompt(self, content: str, output: str = "") -> "AIGuardWorkflow":
        """Add a system prompt to the history of the workflow"""
        return self.add_prompt("system", content, output)

    def add_user_prompt(self, content: str, output: str = "") -> "AIGuardWorkflow":
        """Add a user prompt to the history of the workflow"""
        return self.add_prompt("user", content, output)

    def add_assistant_prompt(self, content: str, output: str = "") -> "AIGuardWorkflow":
        """Add an assistant prompt to the history of the workflow"""
        return self.add_prompt("assistant", content, output)

    def add_prompt(self, role: str, content: str, output: str = "") -> "AIGuardWorkflow":
        """Add a prompt to the history of the workflow"""
        current = Prompt(role=role, content=content)
        if output is not None:
            current["output"] = output
        self._history.append(current)
        return self

    def add_tool(self, tool_name: str, tool_args: Dict[Union[Text, bytes], Any], output: str = "") -> "AIGuardWorkflow":
        """Add a tool execution to the history of the workflow"""
        current = ToolCall(tool_name=tool_name, tool_args=tool_args)
        if output is not None:
            current["output"] = output
        self._history.append(current)
        return self

    def evaluate_tool(
        self, tool_name: str, tool_args: Dict[Union[Text, bytes], Any], tags: Dict[Union[Text, bytes], Any] = {}
    ) -> bool:
        return self._client.evaluate_tool(tool_name, tool_args, history=self._history, tags=tags)

    def evaluate_prompt(self, role: str, content: str, tags: Dict[Union[Text, bytes], Any] = {}) -> bool:
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
        telemetry.telemetry_writer.add_count_metric(TELEMETRY_NAMESPACE.APPSEC, "instrumented.requests", 1, tags)

    def evaluate_tool(
        self,
        tool_name: str,
        tool_args: Dict[Union[Text, bytes], Any],
        history: List[Evaluation] = [],
        tags: Dict[Union[Text, bytes], Any] = {},
    ) -> bool:
        """Evaluate if a tool call is safe to execute.

        Args:
            tool_name: Name of the tool being called
            tool_args: Arguments passed to the tool
            history: History of previous tool calls or prompts
            tags: Tags to set on the created span

        Returns:
            True if tool execution is allowed, False if denied

        Raises:
            AIGuardAbortError: If execution should be aborted
            AIGuardClientError: If evaluation request fails
        """
        tags["ai_guard.target"] = "tool"
        tags["ai_guard.tool_name"] = tool_name
        return self._evaluate(ToolCall(tool_name=tool_name, tool_args=tool_args), history, tags)

    def evaluate_prompt(
        self, role: str, content: str, history: List[Evaluation] = [], tags: Dict[Union[Text, bytes], Any] = {}
    ) -> bool:
        """Evaluate if a prompt is safe to execute.

        Args:
            role: Role of the prompt author
            content: The prompt content
            history: History of previous tool calls or prompts
            tags: Tags to set on the created span

        Returns:
            True if prompt execution is allowed, False if denied

        Raises:
            AIGuardAbortError: If execution should be aborted
            AIGuardClientError: If evaluation request fails
        """
        tags["ai_guard.target"] = "prompt"
        return self._evaluate(Prompt(role=role, content=content), history, tags)

    def _evaluate(self, current: Evaluation, history: List[Evaluation], tags: Dict[Union[Text, bytes], Any]) -> bool:
        """Send evaluation request to AI Guard service."""
        with self._tracer.trace("ai_guard") as span:
            span.set_tags(tags)
            try:
                payload = {"data": {"attributes": {"history": history, "current": current}}}
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
                except Exception as e:
                    value = json.dumps(result, indent=2)[:500]
                    raise AIGuardClientError(f"AI Guard service returned unexpected response format: {value}") from e

                if action not in ACTIONS:
                    raise AIGuardClientError(
                        f"AI Guard service returned unrecognized action: '{action}'. Expected {ACTIONS}"
                    )

                span.set_tag("ai_guard.action", action)
                if reason:
                    span.set_tag("ai_guard.reason", reason)

                self._add_request_to_telemetry(
                    (
                        ("action", action),
                        ("error", "false"),
                    )
                )
                if action == ALLOW:
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
    endpoint: str = ai_guard_config.endpoint,
    timeout: float = 30,
    api_key: str = config._dd_api_key,
    app_key: str = ai_guard_config._dd_app_key,
    tracer: Tracer = ddtracer,
) -> AIGuardClient:
    if not endpoint:
        raise ValueError("AI Guard endpoint URL is required: provide DD_AI_GUARD_ENDPOINT")

    if not api_key or not app_key:
        raise ValueError("Authentication credentials required: provide DD_API_KEY and DD_APP_KEY")

    return AIGuardClient(endpoint, timeout, api_key, app_key, tracer)
