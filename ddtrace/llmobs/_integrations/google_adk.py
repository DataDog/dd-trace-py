from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace._trace.pin import Pin
from ddtrace.internal import core
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import AGENT_MANIFEST
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.google_utils import extract_generation_metrics_google_genai
from ddtrace.llmobs._integrations.google_utils import extract_messages_from_adk_events
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


class GoogleAdkIntegration(BaseLLMIntegration):
    _integration_name = "google_adk"
    _running_agents: Dict[int, List[int]] = {}  # dictionary mapping agent span ID to tool span ID(s)
    _latest_agent = None  # str representing the span ID of the latest agent that was started
    _run_stream_active = False  # bool indicating if the latest agent span was generated from run_stream

    def trace(self, pin: Pin, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Dict[str, Any]) -> Span:
        span = super().trace(pin, operation_id, submit_to_llmobs, **kwargs)
        kind = kwargs.get("kind", None)
        if kind:
            self._register_span(span, kind)
        # always set the component tag
        span.set_tag_str(COMPONENT, self._integration_name)
        return span

    def _set_base_span_tags(
        self, span: Span, model: Optional[Any] = None, provider: Optional[Any] = None, **kwargs
    ) -> None:
        if model:
            span.set_tag("google_adk.request.model", model)
        if provider:
            span.set_tag("google_adk.request.provider", provider)

    def _get_model_and_provider(self, model: Optional[Any]) -> Tuple[str, str]:
        model_name = getattr(model, "model_name", "")
        model_provider = getattr(model, "system", "")
        return model_name, model_provider

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # being used for span kind: one of "agent", "tool", "code_execute"
    ) -> None:
        if operation:
            self._register_span(span, operation)

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
        user_prompt_parts = get_argument_value(args, kwargs, 0, "new_message")
        user_prompt_parts = getattr(user_prompt_parts, "parts", user_prompt_parts)
        if user_prompt_parts:
            user_prompt = "".join([getattr(part, "text", "") for part in user_prompt_parts if hasattr(part, "text")])
        else:
            user_prompt = ""
        result = extract_messages_from_adk_events(response)

        metrics = self.extract_usage_metrics(response, kwargs)
        span._set_ctx_items(
            {
                NAME: agent_name or "Google ADK Agent",
                INPUT_VALUE: user_prompt,
                OUTPUT_VALUE: result,
                METRICS: metrics,
            }
        )

    def _llmobs_set_tags_tool(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any] = None
    ) -> None:
        tool = args[0] if args else kwargs.get("tool")
        tool_args = args[1] if len(args) > 1 else kwargs.get("args")
        tool_call_id = kwargs.get("tool_context", {}).function_call_id

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
        manifest["name"] = agent.name
        manifest["model"] = agent.model.model
        manifest["description"] = agent.description
        manifest["instructions"] = agent.instruction
        manifest["model_configuration"] = str(agent.model_config)
        manifest["session_management"] = {
            "session_id": kwargs.get("session_id", ""),
            "user_id": kwargs.get("user_id", ""),
        }
        manifest["tools"] = self._get_agent_tools(agent.tools)

        span._set_ctx_item(AGENT_MANIFEST, manifest)

    def _llmobs_set_tags_code_execute(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any] = None
    ) -> None:
        stdout = getattr(response, "stdout", None)
        stderr = getattr(response, "stderr", None)
        output = ""
        if stdout:
            output += stdout
            span._set_ctx_item("code.stdout", stdout)
        if stderr:
            output += "/n" + stderr
            span._set_ctx_item("code.stderr", stderr)

        code_input = kwargs.get("code_execution_input", args[1] if len(args) >= 2 and args[1] else None)
        span._set_ctx_items(
            {
                NAME: "Google ADK Code Execute",
                INPUT_VALUE: code_input.code,
                OUTPUT_VALUE: output,
            }
        )

    def _get_agent_tools(self, tools):
        if not tools or not isinstance(tools, list):
            return []
        return [{"name": tool.name, "description": tool.description} for tool in tools]

    def extract_usage_metrics(self, response: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        for event in response:
            usage = extract_generation_metrics_google_genai(event)

            prompt_tokens += int(_get_attr(usage, INPUT_TOKENS_METRIC_KEY, 0) or 0)
            completion_tokens += int(_get_attr(usage, OUTPUT_TOKENS_METRIC_KEY, 0) or 0)
            total_tokens += int(_get_attr(usage, TOTAL_TOKENS_METRIC_KEY, 0) or 0)
        if not prompt_tokens and not completion_tokens and not total_tokens:
            return {}
        return {
            INPUT_TOKENS_METRIC_KEY: prompt_tokens,
            OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
            TOTAL_TOKENS_METRIC_KEY: total_tokens or (prompt_tokens + completion_tokens),
        }

    def _register_span(self, span: Span, kind: Any) -> None:
        if kind == "agent":
            self._register_agent(span)
        elif kind == "tool":
            self._register_tool(span)

    def _register_agent(self, span: Span) -> None:
        self._latest_agent = span.span_id
        self._running_agents[span.span_id] = []

    def _register_tool(self, span: Span) -> None:
        if self._latest_agent is not None:
            self._running_agents[self._latest_agent].append(span.span_id)
