from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple

from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import AGENT_MANIFEST
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
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.trace import Pin
from ddtrace.trace import Span


# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"
    _running_agents: Dict[int, List[int]] = {}  # dictionary mapping agent span ID to tool span ID(s)
    _latest_agent = None  # str representing the span ID of the latest agent that was started
    _run_stream_active = False  # bool indicating if the latest agent span was generated from run_stream

    def trace(self, pin: Pin, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Dict[str, Any]) -> Span:
        span = super().trace(pin, operation_id, submit_to_llmobs, **kwargs)
        kind = kwargs.get("kind", None)
        if kind:
            self._register_span(span, kind)
            span._set_ctx_item(SPAN_KIND, kind)
        return span

    def _set_base_span_tags(self, span: Span, model: Optional[Any] = None, **kwargs) -> None:
        if model:
            model_name, provider = self._get_model_and_provider(model)
            span.set_tag("pydantic_ai.request.model", model_name)
            if provider:
                span.set_tag("pydantic_ai.request.provider", provider)

    def _get_model_and_provider(self, model: Optional[Any]) -> Tuple[str, str]:
        model_name = getattr(model, "model_name", "")
        system = getattr(model, "system", None)
        if system:
            system = PYDANTIC_AI_SYSTEM_TO_PROVIDER.get(system, system)
        return model_name, system

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span_kind = span._get_ctx_item(SPAN_KIND)
        span_links = self._get_span_links(span, span_kind)

        if span_kind == "agent":
            self._llmobs_set_tags_agent(span, args, kwargs, response)
        elif span_kind == "tool":
            self._llmobs_set_tags_tool(span, args, kwargs, response)

        span._set_ctx_items(
            {
                SPAN_KIND: span_kind,
                SPAN_LINKS: span_links,
                MODEL_NAME: span.get_tag("pydantic_ai.request.model") or "",
                MODEL_PROVIDER: span.get_tag("pydantic_ai.request.provider") or "",
            }
        )

    def _llmobs_set_tags_agent(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any]
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
        metrics = self.extract_usage_metrics(response, kwargs)
        span._set_ctx_items(
            {
                NAME: agent_name or "PydanticAI Agent",
                INPUT_VALUE: user_prompt,
                OUTPUT_VALUE: result,
                METRICS: metrics,
            }
        )

    def _llmobs_set_tags_tool(
        self, span: Span, args: List[Any], kwargs: Dict[str, Any], response: Optional[Any] = None
    ) -> None:
        tool_instance = kwargs.get("instance", None)
        tool_call = get_argument_value(args, kwargs, 0, "message", optional=True)
        tool_name = "PydanticAI Tool"
        tool_input: Any = {}
        if tool_call:
            tool_name = getattr(tool_call, "tool_name", "")
            tool_input = getattr(tool_call, "args", {})
        tool_def = getattr(tool_instance, "tool_def", None)
        tool_description = (
            getattr(tool_def, "description", "") if tool_def else getattr(tool_instance, "description", "")
        )
        span._set_ctx_items(
            {
                NAME: tool_name,
                METADATA: {"description": tool_description},
                INPUT_VALUE: tool_input,
            }
        )
        if not span.error:
            # depending on the version, the output may be a ToolReturnPart or the raw response
            output_content = getattr(response, "content", "") or response
            span._set_ctx_item(OUTPUT_VALUE, output_content)

    def _tag_agent_manifest(self, span: Span, kwargs: Dict[str, Any], agent: Any) -> None:
        if not agent:
            return

        manifest: Dict[str, Any] = {}
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
        if kwargs.get("deps", None):
            agent_dependencies = kwargs.get("deps", None)
            manifest["dependencies"] = getattr(agent_dependencies, "__dict__", agent_dependencies)
        manifest["tools"] = self._get_agent_tools(agent)

        span._set_ctx_item(AGENT_MANIFEST, manifest)

    def _get_agent_tools(self, agent: Any) -> List[Dict[str, Any]]:
        """
        Extract tools from the agent and format them to be used in the agent manifest.

        For pydantic-ai < 0.4.4, tools are stored in the agent's _function_tools attribute.
        For pydantic-ai >= 0.4.4, tools are stored in the agent's _function_toolset (tools) and
        _user_toolsets (user-defined toolsets) attributes.
        """
        tools: Dict[str, Any] = {}
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
            tool_dict: Dict[str, Any] = {}
            tool_dict["name"] = tool_name
            if hasattr(tool_instance, "description"):
                tool_dict["description"] = tool_instance.description
            function_schema = getattr(tool_instance, "function_schema", {})
            json_schema = getattr(function_schema, "json_schema", {})
            required_params = {param: True for param in json_schema.get("required", [])}
            parameters: Dict[str, Dict[str, Any]] = {}
            for param, schema in json_schema.get("properties", {}).items():
                param_dict: Dict[str, Any] = {}
                if "type" in schema:
                    param_dict["type"] = schema["type"]
                if param in required_params:
                    param_dict["required"] = True
                parameters[param] = param_dict
            tool_dict["parameters"] = parameters
            formatted_tools.append(tool_dict)
        return formatted_tools

    def extract_usage_metrics(self, response: Any, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        response = kwargs.get("streamed_run_result", None) or response
        usage = None
        try:
            usage = response.usage()
        except Exception:
            return {}
        if usage is None:
            return {}

        prompt_tokens = _get_attr(usage, "request_tokens", 0) or 0
        completion_tokens = _get_attr(usage, "response_tokens", 0) or 0
        total_tokens = _get_attr(usage, "total_tokens", 0) or 0
        if not prompt_tokens and not completion_tokens and not total_tokens:
            return {}
        return {
            INPUT_TOKENS_METRIC_KEY: prompt_tokens,
            OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
            TOTAL_TOKENS_METRIC_KEY: total_tokens or (prompt_tokens + completion_tokens),
        }

    def _get_span_links(self, span: Span, span_kind: Any) -> List[Dict[str, Any]]:
        span_links = []
        if span_kind == "agent":
            for tool_span_id in self._running_agents.pop(span.span_id, []):
                span_links.append(
                    {
                        "span_id": str(tool_span_id),
                        "trace_id": format_trace_id(span.trace_id),
                        "attributes": {"from": "output", "to": "output"},
                    }
                )
        elif span_kind == "tool":
            ancestor = _get_nearest_llmobs_ancestor(span)
            if ancestor:
                span_links.append(
                    {
                        "span_id": str(ancestor.span_id),
                        "trace_id": format_trace_id(ancestor.trace_id),
                        "attributes": {"from": "input", "to": "input"},
                    }
                )
        return span_links

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
