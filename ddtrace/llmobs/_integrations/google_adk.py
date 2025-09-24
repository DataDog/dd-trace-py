from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import AGENT_MANIFEST
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.google_utils import extract_message_from_part_google_genai
from ddtrace.llmobs._integrations.google_utils import extract_messages_from_adk_events
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


class GoogleAdkIntegration(BaseLLMIntegration):
    _integration_name = "google_adk"

    def _set_base_span_tags(
        self, span: Span, model: Optional[Any] = None, provider: Optional[Any] = None, **kwargs
    ) -> None:
        span.set_tag_str(COMPONENT, self._integration_name)
        if model:
            span.set_tag("google_adk.request.model", model)
        if provider:
            span.set_tag("google_adk.request.provider", provider)

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # being used for span kind: one of "agent", "tool", "code_execute"
    ) -> None:
        if operation == "agent":
            self._llmobs_set_tags_agent(span, args, kwargs, response)
        elif operation == "tool":
            self._llmobs_set_tags_tool(span, args, kwargs, response)
        elif operation == "code_execute":
            self._llmobs_set_tags_code_execute(span, args, kwargs, response)

        span._set_ctx_items(
            {
                SPAN_KIND: operation,
                MODEL_NAME: span.get_tag("google_adk.request.model") or "",
                MODEL_PROVIDER: span.get_tag("google_adk.request.provider") or "",
            }
        )

    def _llmobs_set_tags_agent(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any]
    ) -> None:
        agent_instance = kwargs.get("instance", None)
        agent_name = getattr(agent_instance, "name", None)

        self._tag_agent_manifest(span, kwargs, agent_instance)
        new_message = get_argument_value(args, kwargs, 0, "new_message", optional=True) or []
        new_message_parts: list = getattr(new_message, "parts", [])
        new_message_role: str = getattr(new_message, "role", "")
        message = ""
        for part in new_message_parts:
            message += extract_message_from_part_google_genai(part, new_message_role).get("content", "")
        result = extract_messages_from_adk_events(response)

        span._set_ctx_items(
            {
                NAME: agent_name or "Google ADK Agent",
                INPUT_VALUE: message,
                OUTPUT_VALUE: result,
            }
        )

    def _llmobs_set_tags_tool(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any] = None
    ) -> None:
        tool = get_argument_value(args, kwargs, 0, "tool")
        tool_args = get_argument_value(args, kwargs, 1, "args")
        tool_call_id = getattr(kwargs.get("tool_context", {}), "function_call_id", "")

        span._set_ctx_item(OUTPUT_VALUE, response)
        tool_name = getattr(tool, "name", "")
        tool_description = getattr(tool, "description", "")

        span._set_ctx_items(
            {
                NAME: tool_name,
                METADATA: {"description": tool_description},
                INPUT_VALUE: tool_args,
            }
        )

        if tool_call_id:
            core.dispatch(
                DISPATCH_ON_TOOL_CALL,
                (
                    tool_name,
                    safe_json(tool_args),
                    "function",
                    span,
                    tool_call_id,
                ),
            )

    def _tag_agent_manifest(self, span: Span, kwargs: Dict[str, Any], agent: Any) -> None:
        if not agent:
            return

        manifest: Dict[str, Any] = {}

        manifest["framework"] = "Google ADK"
        manifest["name"] = getattr(agent, "name", "")
        manifest["model"] = getattr(getattr(agent, "model", ""), "model", "")
        manifest["description"] = getattr(agent, "description", "")
        manifest["instructions"] = getattr(agent, "instruction", "")
        manifest["model_configuration"] = safe_json(getattr(agent, "model_config", ""))
        manifest["session_management"] = {
            "session_id": kwargs.get("session_id", ""),
            "user_id": kwargs.get("user_id", ""),
        }
        manifest["tools"] = self._get_agent_tools(getattr(agent, "tools", []))

        span._set_ctx_item(AGENT_MANIFEST, manifest)

    def _llmobs_set_tags_code_execute(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any] = None
    ) -> None:
        stdout = getattr(response, "stdout", None)
        stderr = getattr(response, "stderr", None)
        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += "/n" + stderr

        code_input = get_argument_value(args, kwargs, 1, "code_execution_input")
        span._set_ctx_items(
            {
                NAME: "Google ADK Code Execute",
                INPUT_VALUE: getattr(code_input, "code", ""),
                OUTPUT_VALUE: output,
            }
        )

    def _get_agent_tools(self, tools):
        if not tools or not isinstance(tools, list):
            return []
        return [{"name": tool.name, "description": tool.description} for tool in tools]
