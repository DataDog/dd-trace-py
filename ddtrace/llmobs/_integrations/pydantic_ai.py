from typing import Any
from typing import Optional
from typing import Sequence

from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}

_OBSERVED_CTX_KEY = "_dd.pydantic_ai.observed"


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"
    _run_stream_active = False  # bool indicating if the latest agent span was generated from run_stream

    def trace(self, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Any) -> Span:
        span = super().trace(operation_id, submit_to_llmobs, **kwargs)
        kind = kwargs.get("kind", None)
        if kind:
            if kind == "agent":
                span._set_ctx_item(_OBSERVED_CTX_KEY, {"tools": {}, "servers": {}})
            _annotate_llmobs_span_data(span, kind=kind)
        return span

    def _find_nearest_agent_observations(self, span: Span) -> Optional[dict[str, Any]]:
        cur: Optional[Span] = span
        while cur is not None:
            observed: Optional[dict[str, Any]] = cur._get_ctx_item(_OBSERVED_CTX_KEY)
            if observed is not None:
                return observed
            cur = cur._parent
        return None

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
        span_kind = get_llmobs_span_kind(span)

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
        user_prompt = get_argument_value(args, kwargs, 0, "user_prompt", optional=True)
        # AIDEV-NOTE: When callers like VercelAIAdapter pass all messages via message_history
        # without setting user_prompt, we fall back to extracting the last user message from
        # message_history. See https://github.com/DataDog/dd-trace-py/issues/16400
        if user_prompt is None:
            user_prompt = self._extract_user_prompt_from_message_history(kwargs)
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

    @staticmethod
    def _extract_user_prompt_from_message_history(kwargs: dict[str, Any]) -> Optional[str]:
        """Extract the last user prompt from message_history when user_prompt is not provided."""
        message_history = kwargs.get("message_history")
        if not message_history:
            return None
        for message in reversed(message_history):
            for part in reversed(getattr(message, "parts", [])):
                if getattr(part, "part_kind", None) == "user-prompt":
                    content = getattr(part, "content", None)
                    if content is not None:
                        return str(content)
        return None

    def _llmobs_set_tags_tool(
        self, span: Span, args: list[Any], kwargs: dict[str, Any], response: Optional[Any] = None
    ) -> None:
        tool_instance = kwargs.get("instance", None)
        raw_call = (
            get_argument_value(args, kwargs, 0, "call", optional=True)
            or get_argument_value(args, kwargs, 0, "message", optional=True)
            or get_argument_value(args, kwargs, 0, "validated", optional=True)
        )
        # unwrap ValidatedToolCall into tool_instance and tool_call for newer versions of Pydantic AI
        if raw_call is not None and hasattr(raw_call, "args_valid"):
            if tool_instance is None:
                tool_instance = getattr(raw_call, "tool", None)
            tool_call = getattr(raw_call, "call", raw_call)
        else:
            tool_call = raw_call
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

        observed = self._find_nearest_agent_observations(span)
        if tool_call and observed is not None:
            server = self._format_mcp_server(_get_attr(tool_instance, "toolset", None))
            server_name = server["name"] if server else None
            if server:
                observed["servers"].setdefault(server_name, server)
            observed["tools"].setdefault(tool_name, {"description": tool_description, "mcp_server_name": server_name})

        output_val = None
        if not span.error:
            # depending on the version, the output may be a ToolReturnPart or the raw response
            output_val = getattr(response, "content", "") or response

        _annotate_llmobs_span_data(
            span,
            name=tool_name,
            metadata={"description": tool_description},
            input_value=tool_input,
            output_value=output_val,
        )

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
            instructions = agent._instructions
            if isinstance(instructions, list):
                instructions = (
                    " ".join(instructions) if instructions and all(isinstance(i, str) for i in instructions) else None
                )
            manifest["instructions"] = instructions
        if hasattr(agent, "_system_prompts"):
            manifest["system_prompts"] = list(agent._system_prompts)
        observed = self._find_nearest_agent_observations(span)
        manifest["tools"] = self._merge_observed_tools(self._get_agent_tools(agent), observed)
        mcp_servers = self._merge_observed_servers(self._get_mcp_servers(agent, kwargs.get("toolsets")), observed)
        if mcp_servers:
            manifest["dependencies"] = {"mcp_servers": mcp_servers}

        _annotate_llmobs_span_data(span, agent_manifest=manifest)

    @staticmethod
    def _merge_observed_tools(
        agent_tools: list[dict[str, Any]], observed: Optional[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen_names = {tool.get("name") for tool in agent_tools}
        for tool_name, info in (observed or {}).get("tools", {}).items():
            if tool_name in seen_names:
                continue
            entry: dict[str, Any] = {"name": tool_name}
            if info.get("description"):
                entry["description"] = info["description"]
            if info.get("mcp_server_name"):
                entry["mcp_server_name"] = info["mcp_server_name"]
            agent_tools.append(entry)
        return agent_tools

    @staticmethod
    def _merge_observed_servers(
        agent_servers: list[dict[str, Any]], observed: Optional[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        seen_names = {server["name"] for server in agent_servers}
        for server in (observed or {}).get("servers", {}).values():
            if server["name"] not in seen_names:
                agent_servers.append(server)
                seen_names.add(server["name"])
        return agent_servers

    def _get_agent_tools(self, agent: Any) -> list[dict[str, Any]]:
        """
        Extract tools from the agent and format them to be used in the agent manifest.

        For pydantic-ai < 0.4.4, tools are stored in the agent's _function_tools attribute.
        For pydantic-ai >= 0.4.4, tools are stored in the agent's _function_toolset (tools) and
        _user_toolsets (user-defined toolsets) attributes.
        """
        tools: dict[str, Any] = {}
        external_tool_defs: list[Any] = []
        if hasattr(agent, "_function_tools"):
            tools = getattr(agent, "_function_tools", {}) or {}
        elif hasattr(agent, "_user_toolsets") or hasattr(agent, "_function_toolset"):
            user_toolsets: Sequence[Any] = getattr(agent, "_user_toolsets", []) or []
            function_toolset = getattr(agent, "_function_toolset", None)
            combined_toolsets = list(user_toolsets) + [function_toolset] if function_toolset else user_toolsets
            for toolset in combined_toolsets:
                toolset_tools = getattr(toolset, "tools", None)
                if toolset_tools:
                    tools.update(toolset_tools)
                elif hasattr(toolset, "tool_defs"):
                    external_tool_defs.extend(getattr(toolset, "tool_defs", []) or [])

        if not tools and not external_tool_defs:
            return []

        formatted_tools = []
        for tool_name, tool_instance in tools.items():
            tool_dict: dict[str, Any] = {}
            tool_dict["name"] = tool_name
            if hasattr(tool_instance, "description"):
                tool_dict["description"] = tool_instance.description
            function_schema = getattr(tool_instance, "function_schema", {})
            json_schema = getattr(function_schema, "json_schema", {})
            tool_dict["parameters"] = self._format_tool_parameters(json_schema)
            formatted_tools.append(tool_dict)
        for tool_def in external_tool_defs:
            tool_dict = {}
            tool_dict["name"] = getattr(tool_def, "name", "")
            if hasattr(tool_def, "description"):
                tool_dict["description"] = tool_def.description
            tool_dict["parameters"] = self._format_tool_parameters(getattr(tool_def, "parameters_json_schema", {}))
            formatted_tools.append(tool_dict)
        return formatted_tools

    @staticmethod
    def _get_mcp_servers(agent: Any, run_toolsets: Optional[Sequence[Any]] = None) -> list[dict[str, Any]]:
        # `agent.toolsets` honors an active `override(toolsets=)`; per-run toolsets aren't on the agent.
        toolsets = list(getattr(agent, "toolsets", None) or getattr(agent, "_user_toolsets", []) or [])
        toolsets += list(run_toolsets or [])
        # `apply` walks each toolset down to its leaves, descending through wrappers and CombinedToolsets.
        leaves: list[Any] = []
        for toolset in toolsets:
            apply = getattr(toolset, "apply", None)
            if callable(apply):
                apply(leaves.append)
            else:
                leaves.append(toolset)
        candidates = [PydanticAIIntegration._format_mcp_server(leaf) for leaf in leaves]
        candidates += PydanticAIIntegration._get_capability_mcp_servers(agent)
        servers: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for server in candidates:
            if server and server["name"] not in seen_names:
                seen_names.add(server["name"])
                servers.append(server)
        return servers

    @staticmethod
    def _get_capability_mcp_servers(agent: Any) -> list[dict[str, Any]]:
        # `capabilities=[MCP(...)]` (pydantic-ai >= 1.71) is a separate attachment path from toolsets.
        root = getattr(agent, "root_capability", None)
        if root is None:
            return []
        try:
            from pydantic_ai.capabilities import MCP
        except ImportError:
            return []
        leaves: list[Any] = []
        root.apply(leaves.append)
        servers: list[dict[str, Any]] = []
        for cap in leaves:
            # Native MCP runs provider-side, so it never connects locally and has no server_info; the
            # capability id is its identity. Local-only capabilities (native falsy) ride agent.toolsets.
            if not (isinstance(cap, MCP) and cap.native and getattr(cap, "id", None)):
                continue
            server: dict[str, Any] = {"name": cap.id}
            url = getattr(cap, "url", None)
            if url:
                from ddtrace.contrib.internal.trace_utils import _sanitized_url
                from ddtrace.internal.utils.http import strip_query_string

                # A capability URL can embed credentials (userinfo or a `?token=` query param); drop both.
                server["url"] = strip_query_string(_sanitized_url(url))
            description = getattr(cap, "description", None)
            if description:
                server["description"] = description
            servers.append(server)
        return servers

    @staticmethod
    def _format_mcp_server(toolset: Any) -> Optional[dict[str, Any]]:
        # Unwrap wrapper toolsets (e.g. `.prefixed()`) to the underlying MCP server.
        seen: set[int] = set()
        while toolset is not None and hasattr(toolset, "wrapped") and id(toolset) not in seen:
            seen.add(id(toolset))
            toolset = getattr(toolset, "wrapped", None)
        # server_info (name + version) is populated only after the server connects and kept after it
        # disconnects, so a server used in the run stays identifiable here; one never connected is skipped.
        server_info = getattr(toolset, "server_info", None)
        name = getattr(server_info, "name", None)
        if not name:
            return None
        server: dict[str, Any] = {"name": name}
        version = getattr(server_info, "version", None)
        if version:
            server["version"] = version
        return server

    @staticmethod
    def _format_tool_parameters(json_schema: Optional[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        json_schema = json_schema or {}
        required_params = set(json_schema.get("required", []))
        parameters: dict[str, dict[str, Any]] = {}
        for param, schema in json_schema.get("properties", {}).items():
            param_dict: dict[str, Any] = {}
            if "type" in schema:
                param_dict["type"] = schema["type"]
            if param in required_params:
                param_dict["required"] = True
            parameters[param] = param_dict
        return parameters
