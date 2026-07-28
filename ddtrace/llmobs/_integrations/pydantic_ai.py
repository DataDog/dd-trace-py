import functools
import hashlib
import inspect
import json
from typing import Any
from typing import Optional
from typing import Sequence
import urllib.parse

from ddtrace.internal import core
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


# PydanticAI sometimes uses a different provider name than what we expect.
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}


_HTTP_MCP_SCHEMES = frozenset({"http", "https"})
# AIDEV-NOTE: no per-field size caps here. ``_truncate_span_event`` drops only meta.input/output, not the
# manifest; bounding it belongs in the shared writer path, not per-field SDK logic (tracked follow-up).


def _redact_mcp_uri(raw: Any) -> Optional[str]:
    """Scrub an HTTP MCP URL to ``scheme://host[:port]``.

    Allowlist so a new secret-bearing component drops by default rather than by name.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text:
        return None
    parsed = urllib.parse.urlsplit(text)
    # Scheme-less ``host:port`` has no ``//``; re-parse with a leading ``//`` so urlsplit finds the host.
    if not parsed.netloc and "//" not in text:
        parsed = urllib.parse.urlsplit("//" + text)
    elif parsed.scheme and parsed.scheme.lower() not in _HTTP_MCP_SCHEMES:
        return None
    try:
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not host:
        return None
    if ":" in host:  # IPv6 literal: re-bracket so the rebuilt authority stays parseable
        host = "[{}]".format(host)
    if port is not None:
        host = "{}:{}".format(host, port)
    scheme = parsed.scheme.lower() if parsed.scheme and parsed.scheme.lower() in _HTTP_MCP_SCHEMES else "https"
    return "{}://{}".format(scheme, host)


@functools.lru_cache(maxsize=1)
def _output_marker_classes() -> tuple[type, ...]:
    """Output-marker classes present in this pydantic-ai (a version-absent marker is simply omitted)."""
    try:
        from pydantic_ai import output as _output
    except Exception:  # noqa: BLE001 - pydantic_ai.output may not exist on very old versions
        return ()
    classes: list[type] = []
    for name in ("ToolOutput", "NativeOutput", "PromptedOutput", "TextOutput"):
        cls = getattr(_output, name, None)
        if isinstance(cls, type):
            classes.append(cls)
    return tuple(classes)


def _iter_agent_tools(agent: Any):
    """Yield ``(name, tool, fn)`` for the agent's function tools (de-duped first-wins), across pydantic-ai versions."""
    seen: set[str] = set()
    tool_dicts: list[dict[str, Any]] = []
    function_tools = getattr(agent, "_function_tools", None)
    if function_tools:
        tool_dicts.append(function_tools)
    else:
        function_toolset = getattr(agent, "_function_toolset", None)
        user_toolsets: Sequence[Any] = getattr(agent, "_user_toolsets", None) or []
        # Only FunctionToolsets expose a ``{name: tool}`` dict; a custom toolset's ``.tools`` may be a
        # non-dict (captured separately as ``custom`` caps). Gate to FunctionToolset + guard isinstance(dict).
        fn_cls = PydanticAIIntegration._function_toolset_cls()
        toolsets = [t for t in user_toolsets if fn_cls is None or isinstance(t, fn_cls)]
        if function_toolset is not None:
            toolsets.append(function_toolset)
        for toolset in toolsets:
            tools = getattr(toolset, "tools", None)
            if isinstance(tools, dict):
                tool_dicts.append(tools)
    for tools in tool_dicts:
        for name, tool in tools.items():
            if name in seen:
                continue
            seen.add(name)
            fn = getattr(tool, "function", None)
            if fn is None:
                fn = getattr(getattr(tool, "function_schema", None), "function", None)
            yield name, tool, fn


def _dedupe_by_id(items: list[Any], key=lambda item: id(item)) -> list[Any]:
    """Collapse duplicates by ``key`` (default object ``id``), preserving first-seen order.

    Pair call sites pass ``key=lambda p: id(p[0])`` to dedupe ``(fn, flag)`` on the function id.
    """
    seen: set[int] = set()
    unique: list[Any] = []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        unique.append(item)
    return unique


def _callable_name(fn: Any) -> str:
    """Best recoverable name for a callable: its ``__name__``, else a ``functools.partial``'s wrapped
    ``func.__name__``, else the class name of a callable instance. Always a real identifier read off the
    object (never a faked constant), so distinct unnamed callables stay distinguishable in the manifest.
    """
    return getattr(fn, "__name__", None) or getattr(getattr(fn, "func", None), "__name__", None) or type(fn).__name__


def _collect_instructions(agent: Any) -> tuple[list[str], list[Any]]:
    """Gather ``(static_texts, dynamic_fns)`` from an agent's instructions, across pydantic-ai versions.

    Version drift: ``_instructions`` is a ``str`` (<1.63.0) or a mixed ``list`` (>=1.63.0); dynamic fns live
    in ``_instructions_functions`` (<1.63.0) or inline in the list (>=1.63.0). Read both and de-dupe by id.
    """
    static_texts: list[str] = []
    dynamic: list[tuple[Any, bool]] = []  # (fn, reevaluated); instructions rebuild every request -> True
    instructions = getattr(agent, "_instructions", None)
    if isinstance(instructions, (list, tuple)):
        for entry in instructions:
            if isinstance(entry, str):
                static_texts.append(entry)
            elif callable(entry):
                dynamic.append((entry, True))
    elif isinstance(instructions, str):
        static_texts.append(instructions)
    elif callable(instructions):
        dynamic.append((instructions, True))
    for runner in getattr(agent, "_instructions_functions", None) or []:
        fn = getattr(runner, "function", runner)
        if callable(fn):
            dynamic.append((fn, True))
    return static_texts, _dedupe_by_id(dynamic, key=lambda p: id(p[0]))


def _collect_dynamic_system_prompts(agent: Any) -> list[tuple[Any, bool]]:
    """Gather ``(fn, reevaluated)`` dynamic system-prompt resolvers (``SystemPromptRunner`` wrappers,
    unwrap ``.function``); present on every supported version. Static prompts ship verbatim in
    ``agent._system_prompts`` (read directly by the builder), so only resolvers are collected here.
    """
    dynamic: list[tuple[Any, bool]] = []  # (fn, reevaluated); dynamic=True system prompts re-run each step
    for runner in getattr(agent, "_system_prompt_functions", None) or []:
        fn = getattr(runner, "function", runner)
        if callable(fn):
            dynamic.append((fn, bool(getattr(runner, "dynamic", False))))
    return _dedupe_by_id(dynamic, key=lambda p: id(p[0]))


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"
    _running_agents: dict[int, list[int]] = {}  # agent span ID -> tool span ID(s)
    _latest_agent = None  # span ID of the most recently started agent
    _run_stream_active = False  # whether the latest agent span came from run_stream

    def trace(self, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Any) -> Span:
        span = super().trace(operation_id, submit_to_llmobs, **kwargs)
        kind = kwargs.get("kind", None)
        if kind:
            self._register_span(span, kind)
            _annotate_llmobs_span_data(span, kind=kind)
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
        user_prompt = get_argument_value(args, kwargs, 0, "user_prompt", optional=True)
        # AIDEV-NOTE: some callers (e.g. VercelAIAdapter) pass everything via message_history with no
        # user_prompt; fall back to the last user message. See github.com/DataDog/dd-trace-py/issues/16400
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
        # Manifest LAST: it can raise (getsource / model_json_schema) and llmobs_set_tags bails on raise.
        # With name/input/output already set, that degrades to no-manifest instead of blanking the span.
        self._tag_agent_manifest(span, kwargs, agent_instance)

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
        # Newer pydantic-ai wraps the call in a ValidatedToolCall; unwrap it.
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

        output_val = None
        if not span.error:
            # Version-dependent: the output may be a ToolReturnPart or the raw response.
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
        _annotate_llmobs_span_data(span, agent_manifest=self._build_agent_manifest(agent))

    def _build_agent_manifest(self, agent: Any) -> dict[str, Any]:
        """Build the canonical cross-framework agent manifest from a pydantic-ai ``Agent``.

        Additive and capture-only: shipped keys (framework/name/model/model_settings/instructions/
        system_prompts/tools) keep their name, type, and presence; new keys are omit-when-empty.
        Function-bearing fields carry ``{name, source_hash?}`` (source hashed, never emitted). Fields the
        framework does not expose are omitted, never faked. The keys below are the schema.
        """
        manifest: dict[str, Any] = {"framework": "PydanticAI"}
        manifest["name"] = agent.name if getattr(agent, "name", None) else "PydanticAI Agent"

        model = getattr(agent, "model", None)
        if model:
            model_name, model_provider = self._get_model_and_provider(model)
            if model_name:
                manifest["model"] = model_name
            if model_provider:
                manifest["model_provider"] = model_provider
        if hasattr(agent, "model_settings"):
            manifest["model_settings"] = load_data_value(agent.model_settings)

        # Static text -> shipped ``instructions``/``system_prompts``; dynamic resolvers -> extra_instructions.
        static_instructions, dynamic_instructions = _collect_instructions(agent)
        dynamic_system_prompts = _collect_dynamic_system_prompts(agent)
        # ``\n`` join mirrors pydantic-ai's own renderer; a space would collapse ``["a b"]`` and ``["a","b"]``.
        instructions_text = "\n".join(t for t in static_instructions if t)
        manifest["instructions"] = instructions_text or None
        if hasattr(agent, "_system_prompts"):
            manifest["system_prompts"] = agent._system_prompts
        extra_instructions = self._build_extra_instructions(dynamic_instructions, dynamic_system_prompts)
        if extra_instructions:
            manifest["extra_instructions"] = extra_instructions

        # Capability surface: flat ``tools`` kept (backward compat) + unified ``capabilities`` superset.
        manifest["tools"] = self._get_agent_tools(agent)
        capabilities = self._build_capabilities(agent)
        if capabilities:
            manifest["capabilities"] = capabilities

        # Additive sections. Registration order is preserved (semantic, e.g. guardrails are a pipeline);
        # reordering is a backend concern.
        handoffs = self._get_agent_handoffs(agent)
        if handoffs:
            manifest["handoffs"] = handoffs
        guardrails = self._get_guardrails(agent)
        if guardrails:
            manifest["guardrails"] = guardrails
        output_type = self._get_agent_output_type(agent)
        if output_type:
            manifest["output_type"] = output_type
        memory_policies = self._get_history_processors(agent)
        if memory_policies:
            manifest["memory_policies"] = memory_policies
        tool_transforms = self._get_tool_transforms(agent)
        if tool_transforms:
            manifest["tool_transforms"] = tool_transforms
        agent_settings = self._get_agent_settings(agent)
        if agent_settings:
            manifest["agent_settings"] = agent_settings

        # Display-only metadata, deep-copied via a json round-trip; unserializable metadata is skipped.
        agent_metadata = getattr(agent, "_metadata", None)
        if isinstance(agent_metadata, dict) and agent_metadata:
            serialized = safe_json(agent_metadata)
            if serialized is not None:
                manifest["metadata"] = json.loads(serialized)

        return manifest

    def _build_extra_instructions(
        self, dynamic_instructions: list[Any], dynamic_system_prompts: list[Any]
    ) -> list[dict[str, Any]]:
        """Ordered bucket of DYNAMIC prompt resolvers (``dynamic_instructions`` then ``dynamic_system_prompt``),
        each ``{type, content:{name, source_hash?, reevaluated}}``. Static text stays in the shipped
        ``instructions``/``system_prompts``; order preserved.
        """
        entries: list[dict[str, Any]] = []
        for kind, pairs in (
            ("dynamic_instructions", dynamic_instructions),
            ("dynamic_system_prompt", dynamic_system_prompts),
        ):
            for fn, reevaluated in pairs:
                for described in self._describe_functions([fn]):
                    described["reevaluated"] = reevaluated
                    entries.append({"type": kind, "content": described})
        return entries

    def _build_capabilities(self, agent: Any) -> list[dict[str, Any]]:
        """Unified capability list (function tools / builtins / MCP servers / custom toolsets), each
        ``{name, type, description?, content}`` in assembly order.

        pydantic-ai has no sub-agent construct, so delegation is not inferred; it is observable in the
        trace's nested agent span. Function tools are ``type: "tool"``.
        """
        capabilities: list[dict[str, Any]] = []
        for name, tool_instance, _fn in _iter_agent_tools(agent):
            entry: dict[str, Any] = {
                "name": name,
                "type": "tool",
                "content": {"schema": self._tool_parameters(tool_instance)},
            }
            if hasattr(tool_instance, "description") and tool_instance.description:
                entry["description"] = tool_instance.description
            capabilities.append(entry)
        for tool in getattr(agent, "_builtin_tools", None) or []:
            kind = getattr(tool, "kind", None) or type(tool).__name__
            if kind:
                capabilities.append({"name": kind, "type": "builtin", "content": {}})
        for server in self._get_mcp_servers(agent):
            content = {"uri": server["uri"]} if server.get("uri") else {}
            capabilities.append({"name": server["name"], "type": "mcp", "content": content})
        for toolset in self._get_custom_toolsets(agent):
            capabilities.append({"name": toolset["name"], "type": "custom", "content": {}})
        return capabilities

    def _get_agent_tools(self, agent: Any) -> list[dict[str, Any]]:
        """Shipped flat ``tools`` list ``{name, description?, parameters}`` in registration order, kept for
        backward compatibility. A function tool also appears in ``capabilities`` (accepted duplication).
        """
        tools: list[dict[str, Any]] = []
        for tool_name, tool_instance, _fn in _iter_agent_tools(agent):
            entry: dict[str, Any] = {"name": tool_name}
            if hasattr(tool_instance, "description") and tool_instance.description:
                entry["description"] = tool_instance.description
            entry["parameters"] = self._tool_parameters(tool_instance)
            tools.append(entry)
        return tools

    @staticmethod
    def _describe_functions(fns: list[Any]) -> list[dict[str, Any]]:
        """Describe each function as ``{name, source_hash?}`` (the shared descriptor shape).

        AIDEV-NOTE: hash ``inspect.getsource`` instead of shipping the body: change-detection for versioning
        without leaking code/secrets (which can't be scrubbed like a URL). ``signature``/``doc`` are dropped
        for the same reason. The hash covers the decorator + indentation, so it moves on reformatting and is
        not comparable across call sites; not a semantic fingerprint.
        """
        described: list[dict[str, Any]] = []
        for fn in fns:
            entry: dict[str, Any] = {"name": _callable_name(fn)}
            try:
                source: Optional[str] = inspect.getsource(fn)
            except (OSError, TypeError):
                # No retrievable source (lambda, REPL-defined, C-implemented) -> name-only, no hash.
                source = None
            if source is not None:
                entry["source_hash"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
            described.append(entry)
        return described

    @classmethod
    def _get_history_processors(cls, agent: Any) -> list[dict[str, Any]]:
        """Describe the agent's message-history processors (its memory policy).

        ``agent.history_processors`` is a public list of bare callables (no ``.function`` unwrap); order
        preserved (``[trim, summarize]`` != ``[summarize, trim]``).
        """
        processors = getattr(agent, "history_processors", None) or []
        fns = _dedupe_by_id([fn for fn in processors if callable(fn)])
        return cls._describe_functions(fns)

    @staticmethod
    def _tool_parameters(tool_instance: Any) -> dict[str, dict[str, Any]]:
        """Extract ``{param: {type?, required?}}`` from a tool's ``function_schema.json_schema``."""
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
        return parameters

    def _get_agent_settings(self, agent: Any) -> dict[str, Any]:
        """Build the agent's ``agent_settings`` (a flat additive key); only fields present.

        ``retries`` is the output-validation retry budget (``_max_result_retries``/``_max_output_retries``);
        ``tool_retries`` is the distinct per-tool retry budget (``_max_tool_retries``).
        """
        settings: dict[str, Any] = {}
        # >1.63.0 renamed _max_result_retries -> _max_output_retries; fall back to the successor.
        retries = getattr(agent, "_max_result_retries", None)
        if not isinstance(retries, int):
            retries = getattr(agent, "_max_output_retries", None)
        if isinstance(retries, int):
            settings["retries"] = retries
        tool_retries = getattr(agent, "_max_tool_retries", None)
        if isinstance(tool_retries, int):
            settings["tool_retries"] = tool_retries
        end_strategy = getattr(agent, "end_strategy", None)
        if isinstance(end_strategy, str):
            settings["end_strategy"] = end_strategy
        deps_type = getattr(agent, "_deps_type", None)
        # Omit the "no deps" default, ``NoneType`` (<2.x) or ``object`` (>=2.x), so it isn't noise.
        if isinstance(deps_type, type) and deps_type not in (type(None), object):
            settings["deps_type"] = deps_type.__name__
        return settings

    def _get_agent_output_type(self, agent: Any) -> dict[str, Any]:
        """``output_type`` ``{name, schema?}`` from ``agent.output_type`` (callables go to ``handoffs``).

        A multi-output union (``[Fruit, Vehicle]`` / ``NativeOutput([...])``) is captured in full: ``name``
        joins the members and ``schema`` is the union ``anyOf``, so any alternative change is reflected.
        """
        if not hasattr(agent, "output_type"):
            return {}
        # Only type candidates; output-function callables are captured as ``handoffs``, not here.
        candidates = [c for c in self._unwrap_output_markers(agent.output_type) if not self._is_output_function(c)]
        if not candidates:
            return {}
        output_type: dict[str, Any] = {"name": " | ".join(getattr(c, "__name__", None) or str(c) for c in candidates)}
        # Emit a schema when any alternative is a pydantic model (a union of bare scalars has none worth capturing).
        if any(isinstance(c, type) and self._is_pydantic_model(c) for c in candidates):
            schema = self._output_schema(candidates)
            if schema is not None:
                output_type["schema"] = schema
        return output_type

    @staticmethod
    def _output_schema(candidates: list[Any]) -> Optional[dict[str, Any]]:
        """JSON schema for a single pydantic model or a union of >1 members; ``None`` on generation
        failure (name-only fallback).

        A single model MUST use ``model_json_schema`` (inline ``properties``); the ``TypeAdapter`` union
        form wraps members in ``$ref``/``$defs``, so it is used ONLY for genuine multi-member unions.
        """
        try:
            if len(candidates) == 1:
                schema: dict[str, Any] = candidates[0].model_json_schema()
            else:
                from typing import Union

                from pydantic import TypeAdapter

                schema = TypeAdapter(Union[tuple(candidates)]).json_schema()
        except Exception:  # noqa: BLE001 - schema/union generation can raise on exotic models
            return None
        return schema

    def _get_guardrails(self, agent: Any) -> list[dict[str, Any]]:
        """Describe output guardrails (``@agent.output_validator`` -> ``agent._output_validators``), each
        ``{name, source_hash?}`` (see ``_describe_functions``). Validators are a chained pipeline, so order
        is semantic and preserved (not sorted).
        """
        validators = getattr(agent, "_output_validators", None) or []
        fns = _dedupe_by_id([getattr(v, "function", v) for v in validators])
        return self._describe_functions([fn for fn in fns if callable(fn)])

    def _get_tool_transforms(self, agent: Any) -> list[dict[str, Any]]:
        """Describe per-run tool-set rewriters (``prepare_tools`` / ``prepare_output_tools``).

        Each is a single callable on ``agent._prepare_tools`` / ``agent._prepare_output_tools`` (absent on
        agents that set neither); describe it and tag the ``scope`` it applies to.
        """
        transforms: list[dict[str, Any]] = []
        for attr, scope in (("_prepare_tools", "tools"), ("_prepare_output_tools", "output_tools")):
            fn = getattr(agent, attr, None)
            if callable(fn):
                for described in self._describe_functions([fn]):
                    described["scope"] = scope
                    transforms.append(described)
        return transforms

    @staticmethod
    def _mcp_server_cls() -> Optional[type]:
        """Return ``pydantic_ai.mcp.MCPServer``, or ``None`` when the optional ``mcp`` extra is absent."""
        try:
            from pydantic_ai.mcp import MCPServer
        except Exception:  # noqa: BLE001 - the ``mcp`` extra may not be installed
            return None
        mcp_cls: type = MCPServer
        return mcp_cls

    @staticmethod
    def _function_toolset_cls() -> Optional[type]:
        """Return ``pydantic_ai.toolsets.FunctionToolset`` for isinstance filtering, or ``None`` (nothing skipped)."""
        try:
            from pydantic_ai.toolsets import FunctionToolset
        except Exception:  # noqa: BLE001 - toolset module layout varies by version
            return None
        fn_cls: type = FunctionToolset
        return fn_cls

    @staticmethod
    def _toolset_name(toolset: Any) -> str:
        """Toolset/MCP-server name: the user-set ``id`` else the class name.

        AIDEV-NOTE: never read ``label``; it falls back to ``repr(self)``, leaking the connection config
        (URL secrets, stdio command/args) past ``_redact_mcp_uri``.
        """
        return getattr(toolset, "id", None) or type(toolset).__name__

    def _get_mcp_servers(self, agent: Any) -> list[dict[str, Any]]:
        """List MCP servers as ``{name, uri?}`` (the ``mcp_servers`` key names the type; no ``type`` field).

        Only an HTTP ``.url`` is emitted (redacted); a stdio ``.command`` (basename can be a secret) is never emitted.
        """
        servers: list[dict[str, Any]] = []
        mcp_cls = self._mcp_server_cls()
        if mcp_cls is None:
            return servers
        for toolset in getattr(agent, "_user_toolsets", None) or []:
            if not isinstance(toolset, mcp_cls):
                continue
            entry: dict[str, Any] = {"name": self._toolset_name(toolset)}
            uri = _redact_mcp_uri(getattr(toolset, "url", None))
            if uri:
                entry["uri"] = uri
            servers.append(entry)
        return servers

    def _get_custom_toolsets(self, agent: Any) -> list[dict[str, Any]]:
        """List non-MCP, non-function user toolsets as ``{name}`` so none is silently dropped."""
        mcp_cls = self._mcp_server_cls()
        fn_cls = self._function_toolset_cls()
        custom: list[dict[str, Any]] = []
        for toolset in getattr(agent, "_user_toolsets", None) or []:
            if mcp_cls is not None and isinstance(toolset, mcp_cls):
                continue
            if fn_cls is not None and isinstance(toolset, fn_cls):
                continue
            custom.append({"name": self._toolset_name(toolset)})
        return custom

    def _get_agent_handoffs(self, agent: Any) -> list[dict[str, Any]]:
        """Output-function callables in ``agent.output_type``, each ``{tool_name, handoff_description?}``.

        No ``agent_name``: pydantic exposes no reliable static link to a target agent, so it is not inferred
        (delegation is observable in the trace's nested agent span).
        """
        if not hasattr(agent, "output_type"):
            return []
        handoffs: list[dict[str, Any]] = []
        for marker, fn in self._iter_output_functions(agent.output_type):
            handoff: dict[str, Any] = {"tool_name": _callable_name(fn)}
            description = getattr(marker, "description", None) or getattr(fn, "__doc__", None)
            if description:
                # Routing text, not code, emitted as-is (short by nature; the 5MB event cap is the backstop).
                handoff["handoff_description"] = description
            handoffs.append(handoff)
        return handoffs

    # The wrapped target lives under a different attr per marker class, so read all three.
    _OUTPUT_MARKER_ATTRS = ("output", "outputs", "output_function")

    @classmethod
    def _marker_target(cls, item: Any) -> tuple[Any, Any]:
        """Return ``(marker, inner)`` if ``item`` is an output marker, else ``(None, item)``.

        The ``isinstance`` gate is load-bearing: the wrapper attr names are not exclusive to marker
        classes, so a plain dataclass/NamedTuple/Enum ``output_type`` could carry a same-named member.
        """
        if not isinstance(item, _output_marker_classes()):
            return None, item
        for attr in cls._OUTPUT_MARKER_ATTRS:
            inner = getattr(item, attr, None)
            if inner is not None:
                return item, inner
        return None, item

    @classmethod
    def _unwrap_output_markers(cls, output_type: Any) -> list[Any]:
        """Flatten ``agent.output_type`` into candidate types: unwrap markers and expand unions."""
        candidates: list[Any] = []
        for item in output_type if isinstance(output_type, (list, tuple)) else [output_type]:
            _, inner = cls._marker_target(item)
            candidates.extend(inner if isinstance(inner, (list, tuple)) else [inner])
        return candidates

    @classmethod
    def _iter_output_functions(cls, output_type: Any):
        """Yield ``(marker, function)`` pairs for output-function callables in ``output_type`` (types are skipped)."""
        for item in output_type if isinstance(output_type, (list, tuple)) else [output_type]:
            marker, target = cls._marker_target(item)
            for candidate in target if isinstance(target, (list, tuple)) else [target]:
                if cls._is_output_function(candidate):
                    yield marker, candidate

    @staticmethod
    def _is_pydantic_model(candidate: Any) -> bool:
        """True if ``candidate`` is a ``pydantic.BaseModel`` subclass (yields a JSON schema)."""
        from pydantic import BaseModel

        return isinstance(candidate, type) and issubclass(candidate, BaseModel)

    @staticmethod
    def _is_output_function(candidate: Any) -> bool:
        """True if ``candidate`` is an output *function* (callable but not a class)."""
        return callable(candidate) and not isinstance(candidate, type)

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
