from typing import Any
from typing import Optional
from typing import Sequence

from ddtrace._trace.pin import Pin
from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"
    _running_agents: dict[int, list[int]] = {}  # dictionary mapping agent span ID to tool span ID(s)
    _latest_agent = None  # str representing the span ID of the latest agent that was started
    _run_stream_active = False  # bool indicating if the latest agent span was generated from run_stream

    def trace(self, pin: Pin, operation_id: str, submit_to_llmobs: bool = False, **kwargs: dict[str, Any]) -> Span:
        span = super().trace(pin, operation_id, submit_to_llmobs, **kwargs)
        kind = kwargs.get("kind", None)
        if kind:
            self._register_span(span, kind)
            # Store kind as internal marker for later use in _llmobs_set_tags
            span._set_ctx_item(SPAN_KIND, kind)
        return span

    def _set_base_span_tags(self, span: Span, model: Optional[Any] = None, **kwargs) -> None:
        if model:
            model_name, provider = self._get_model_and_provider(model)
            span.set_tag("pydantic_ai.request.model", model_name)
            if provider:
                span.set_tag("pydantic_ai.request.provider", provider)

    def _get_model_and_provider(self, model: Optional[Any]) -> tuple[str, str]:
        model_name = getattr(model, "model_name", "")
        system = getattr(model, "system", None)
        if system:
            system = PYDANTIC_AI_SYSTEM_TO_PROVIDER.get(system, system)
        return model_name, system

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span_kind = span._get_ctx_item(SPAN_KIND)

        if span_kind == "agent":
            self._llmobs_set_tags_agent(span, args, kwargs, response)
        elif span_kind == "tool":
            self._llmobs_set_tags_tool(span, args, kwargs, response)

        _annotate_llmobs_span_data(
            span,
            kind=span_kind,
            model_name=span.get_tag("pydantic_ai.request.model") or "",
            model_provider=span.get_tag("pydantic_ai.request.provider") or "",
        )

    def _llmobs_set_tags_agent(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any]
    ) -> None:
        from pydantic_ai.agent import AgentRun

        agent_instance = kwargs.get("instance", None)
        agent_name = getattr(agent_instance, "name", None)
        self._tag_agent_manifest(span, kwargs, agent_instance)
        user_prompt = get_argument_value(args, kwargs, 0, "user_prompt")
        result = response
        if isinstance(result, AgentRun) and hasattr(result, "result"):
            result = getattr(result.result, "output", "")
        elif isinstance(result, tuple) and len(result) == 2:
            model_response, _ = result
            result = ""
            for part in getattr(model_response, "parts", []):
                if hasattr(part, "content"):
                    result += part.content
                elif hasattr(part, "args_as_json_str"):
                    result += part.args_as_json_str()
        _annotate_llmobs_span_data(
            span,
            name=agent_name or "PydanticAI Agent",
            input_value=user_prompt,
            output_value=result,
        )

    def _llmobs_set_tags_tool(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any] = None
    ) -> None:
        tool_instance = kwargs.get("instance", None)
        tool_call = get_argument_value(args, kwargs, 0, "call", optional=True) or get_argument_value(
            args, kwargs, 0, "message", optional=True
        )
        tool_name = "PydanticAI Tool"
        tool_input: Any = {}
        tool_id = ""
        if tool_call:
            tool_name = _get_attr(tool_call, "tool_name", "")
            tool_input = _get_attr(tool_call, "args", "") or ""
            tool_id = _get_attr(tool_call, "tool_call_id", "")
        tool_def = _get_attr(tool_instance, "tool_def", None)
        tool_description = (
            _get_attr(tool_def, "description", "") if tool_def else _get_attr(tool_instance, "description", "")
        )
        _annotate_llmobs_span_data(
            span,
            name=tool_name,
            metadata={"description": tool_description},
            input_value=tool_input,
        )
        if not span.error:
            # depending on the version, the output may be a ToolReturnPart or the raw response
            output_content = getattr(response, "content", "") or response
            _annotate_llmobs_span_data(span, output_value=output_content)

        core.dispatch(
            DISPATCH_ON_TOOL_CALL,
            (
                tool_name,
                safe_json(tool_input) if not isinstance(tool_input, str) else tool_input,
                "function",
                span,
                tool_id,
            ),
        )

    def _tag_agent_manifest(self, span: Span, kwargs: dict[str, Any], agent: Any) -> None:
        if not agent:
            return

        manifest: dict[str, Any] = {}
        manifest["framework"] = "PydanticAI"
        manifest["name"] = agent.name if hasattr(agent, "name") and agent.name else "PydanticAI Agent"
        model = getattr(agent, "model", None)
        if model:
            model_name, _ = self._get_model_and_provider(model)
            if model_name:
                manifest["model"] = model_name
        if hasattr(agent, "model_settings"):
            manifest["model_settings"] = agent.model_settings
        if hasattr(agent, "_instructions"):
            manifest["instructions"] = agent._instructions
        if hasattr(agent, "_system_prompts"):
            manifest["system_prompts"] = agent._system_prompts
        manifest["tools"] = self._get_agent_tools(agent)

        _annotate_llmobs_span_data(span, agent_manifest=manifest)

    def _get_agent_tools(self, agent: Any) -> list[dict[str, Any]]:
        """
        Extract tools from the agent and format them to be used in the agent manifest.

        For pydantic-ai < 0.4.4, tools are stored in the agent's _function_tools attribute.
        For pydantic-ai >= 0.4.4, tools are stored in the agent's _function_toolset (tools) and
        _user_toolsets (user-defined toolsets) attributes.
        """
        tools: dict[str, Any] = {}
        if hasattr(agent, "_function_tools"):
            tools = getattr(agent, "_function_tools", {}) or {}
        elif hasattr(agent, "_user_toolsets") or hasattr(agent, "_function_toolset"):
            user_toolsets: Sequence[Any] = getattr(agent, "_user_toolsets", []) or []
            function_toolset = getattr(agent, "_function_toolset", None)
            combined_toolsets = list(user_toolsets) + [function_toolset] if function_toolset else user_toolsets
            for toolset in combined_toolsets:
                tools.update(getattr(toolset, "tools", {}) or {})

        if not tools:
            return []

        formatted_tools = []
        for tool_name, tool_instance in tools.items():
            tool_dict: dict[str, Any] = {}
            tool_dict["name"] = tool_name
            if hasattr(tool_instance, "description"):
                tool_dict["description"] = tool_instance.description
            function_schema = getattr(tool_instance, "function_schema", {})
            json_schema = getattr(function_schema, "json_schema", {})
            required_params = {param: True for param in json_schema.get("required", [])}
            parameters: dict[str, dict[str, Any]] = {}
            for param, schema in json_schema.get("properties", {}).items():
                param_dict: dict[str, Any] = {}
                if "type" in schema:
                    param_dict["type"] = schema["type"]
                if param in required_params:
                    param_dict["required"] = True
                parameters[param] = param_dict
            tool_dict["parameters"] = parameters
            formatted_tools.append(tool_dict)
        return formatted_tools

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
