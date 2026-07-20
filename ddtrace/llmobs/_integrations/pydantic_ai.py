import functools
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
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


# PydanticAI sometimes uses a different provider name than what we expect.
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}


_HTTP_MCP_SCHEMES = frozenset({"http", "https"})
# AIDEV-NOTE: size caps -- the manifest rides every agent span and ``_truncate_span_event`` does not
# drop it, so an oversized field would evict the user's real ``meta.input``/``meta.output`` under the
# span size cap.
_OUTPUT_SCHEMA_MAX_BYTES = 8192
_METADATA_MAX_BYTES = 8192
_PROMPT_SOURCE_MAX_BYTES = 8192


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
    # A scheme-less ``host:port/...`` has no ``//``, so ``urlsplit`` mistakes the host for the scheme;
    # re-parse with a leading ``//`` to recover the authority.
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
    if ":" in host:  # IPv6 literal -- re-bracket so the rebuilt authority stays parseable
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
        toolsets = list(user_toolsets)
        if function_toolset is not None:
            toolsets.append(function_toolset)
        for toolset in toolsets:
            tool_dicts.append(getattr(toolset, "tools", None) or {})
    for tools in tool_dicts:
        for name, tool in tools.items():
            if name in seen:
                continue
            seen.add(name)
            fn = getattr(tool, "function", None)
            if fn is None:
                fn = getattr(getattr(tool, "function_schema", None), "function", None)
            yield name, tool, fn


def _dedupe_by_id(fns: list[Any]) -> list[Any]:
    """Return ``fns`` with duplicate objects (same ``id``) collapsed, preserving first-seen order."""
    seen: set[int] = set()
    unique: list[Any] = []
    for fn in fns:
        if id(fn) in seen:
            continue
        seen.add(id(fn))
        unique.append(fn)
    return unique


def _dedupe_pairs(pairs: list[tuple[Any, bool]]) -> list[tuple[Any, bool]]:
    """Dedupe ``(fn, flag)`` pairs by function ``id`` (first-seen wins), preserving order."""
    seen: set[int] = set()
    unique: list[tuple[Any, bool]] = []
    for fn, flag in pairs:
        if id(fn) in seen:
            continue
        seen.add(id(fn))
        unique.append((fn, flag))
    return unique


def _sorted_definition_list(items: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Sort an order-incidental definition list into a total, deterministic (reversal-invariant) order.

    Sorts by the field(s) in ``keys``, ties broken by the full serialized entry -- so entries sharing the
    primary key but differing in content (e.g. two MCP servers, same name, different URL) still order
    deterministically. Order-SEMANTIC lists (prompt functions, history processors) must NOT use this.
    """
    return sorted(items, key=lambda item: (tuple(item.get(k) or "" for k in keys), safe_json(item) or ""))


def _collect_instructions(agent: Any) -> tuple[list[str], list[Any]]:
    """Gather ``(static_texts, dynamic_fns)`` from an agent's instructions, across pydantic-ai versions.

    Version drift (verified 0.8.1 / 1.0.0 / 1.63.0): ``agent._instructions`` is a plain ``str`` on
    <1.63.0 and a ``list`` mixing static strings with raw callables on >=1.63.0; the dynamic instruction
    functions live in ``agent._instructions_functions`` (``SystemPromptRunner`` wrappers, unwrap
    ``.function``) on <1.63.0 but that attribute is absent on >=1.63.0. Reading both sources and de-duping
    the callables by ``id`` captures the dynamic functions on every version.
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
    return static_texts, _dedupe_pairs(dynamic)


def _collect_system_prompts(agent: Any) -> tuple[list[str], list[Any]]:
    """Gather ``(static_texts, dynamic_fns)`` from an agent's system prompts.

    ``agent._system_prompts`` is a ``tuple[str]`` and ``agent._system_prompt_functions`` a list of
    ``SystemPromptRunner`` wrappers (unwrap ``.function``) on every supported version.
    """
    static_texts: list[str] = []
    dynamic: list[tuple[Any, bool]] = []  # (fn, reevaluated); dynamic=True system prompts re-run each step
    for entry in getattr(agent, "_system_prompts", None) or ():
        if isinstance(entry, str):
            static_texts.append(entry)
    for runner in getattr(agent, "_system_prompt_functions", None) or []:
        fn = getattr(runner, "function", runner)
        if callable(fn):
            dynamic.append((fn, bool(getattr(runner, "dynamic", False))))
    return static_texts, _dedupe_pairs(dynamic)


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
        """Build the canonical agent manifest (the cross-framework schema) from a pydantic-ai ``Agent``.

        ADDITIVE over the shipped manifest. Shipped keys keep their name + type: ``framework`` / ``name`` /
        ``model`` / ``model_settings`` / ``instructions`` (str|None) / ``system_prompts`` (list) / ``tools``
        (flat list, kept for backward compatibility). New additive keys: ``model_provider``;
        ``extra_instructions`` (ordered typed DYNAMIC resolvers -- dynamic instructions + dynamic system
        prompts); ``capabilities`` (unified typed superset of tools / sub-agents / builtins / MCP servers /
        custom toolsets, each ``{name, type, description?, content}`` -- function tools appear here AND in the
        flat ``tools``, an accepted duplication); ``handoffs``; ``guardrails``; ``output_type``;
        ``history_processors``; ``tool_transforms``; ``settings``; ``metadata``.

        pydantic-only: fields the framework does not expose (``max_turns``, ``parallel_tool_calls``,
        ``tool_use_behavior``, input/tool guardrails, ...) are OMITTED, never faked. Empty additive keys are
        omitted; shipped ``instructions`` / ``system_prompts`` / ``tools`` / ``model_settings`` mirror the
        prior manifest's presence.
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
            manifest["model_settings"] = agent.model_settings

        # Instructions: shipped ``instructions`` (string) + shipped ``system_prompts`` (list); the runtime
        # resolvers go in the additive ``extra_instructions`` bucket (dynamics only).
        static_instructions, dynamic_instructions = _collect_instructions(agent)
        _, dynamic_system_prompts = _collect_system_prompts(agent)
        instructions_text = " ".join(t for t in static_instructions if t)
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

        # Other additive sections (flat; pydantic-real fields only).
        handoffs = _sorted_definition_list(self._get_agent_handoffs(agent), ("name",))
        if handoffs:
            manifest["handoffs"] = handoffs
        guardrails = _sorted_definition_list(self._get_guardrails(agent), ("name",))
        if guardrails:
            manifest["guardrails"] = guardrails
        output_type = self._get_agent_output_type(agent)
        if output_type:
            manifest["output_type"] = output_type
        history_processors = self._get_history_processors(agent)
        if history_processors:
            manifest["history_processors"] = history_processors
        tool_transforms = self._get_tool_transforms(agent)
        if tool_transforms:
            manifest["tool_transforms"] = tool_transforms
        settings = self._get_agent_settings(agent)
        if settings:
            manifest["settings"] = settings

        # Display-only metadata (deep-copied via json round-trip, size-bounded).
        agent_metadata = getattr(agent, "_metadata", None)
        if isinstance(agent_metadata, dict) and agent_metadata:
            serialized = safe_json(agent_metadata)
            if serialized is None or len(serialized) > _METADATA_MAX_BYTES:
                manifest["metadata_truncated"] = True
            else:
                manifest["metadata"] = json.loads(serialized)

        return manifest

    def _build_extra_instructions(
        self, dynamic_instructions: list[Any], dynamic_system_prompts: list[Any]
    ) -> list[dict[str, Any]]:
        """Additive ordered bucket of DYNAMIC prompt resolvers only -- ``dynamic_instructions`` then
        ``dynamic_system_prompt`` -- each ``{type, content:{name, signature?, doc?, source?, reevaluated}}``.

        The static instruction text stays in the shipped ``instructions`` (string) and static system prompts
        in the shipped ``system_prompts`` (list); this bucket carries the resolvers only. Order preserved.
        """
        entries: list[dict[str, Any]] = []
        for kind, pairs in (
            ("dynamic_instructions", dynamic_instructions),
            ("dynamic_system_prompt", dynamic_system_prompts),
        ):
            for fn, reevaluated in pairs:
                for described in self._describe_prompt_functions([fn]):
                    described["reevaluated"] = reevaluated
                    entries.append({"type": kind, "content": described})
        return entries

    def _build_capabilities(self, agent: Any) -> list[dict[str, Any]]:
        """Unified capability list -- function tools / sub-agents (delegating tools) / builtins / MCP servers /
        custom toolsets, each ``{name, type, description?, content}``. Order-incidental -> sorted.
        """
        capabilities: list[dict[str, Any]] = []
        for name, tool_instance, fn in _iter_agent_tools(agent):
            content: dict[str, Any] = {"schema": self._tool_parameters(tool_instance)}
            agent_name = self._referenced_agent_name(fn) if callable(fn) else None
            if agent_name is not None:
                if agent_name:
                    content["agent"] = agent_name
                entry: dict[str, Any] = {"name": name, "type": "sub_agent", "content": content}
            else:
                entry = {"name": name, "type": "tool", "content": content}
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
        return _sorted_definition_list(capabilities, ("type", "name"))

    def _get_agent_tools(self, agent: Any) -> list[dict[str, Any]]:
        """The shipped flat ``tools`` list -- ``{name, description?, parameters}`` in registration order
        (semantic, NOT sorted). Kept for backward compatibility alongside the unified ``capabilities``; a
        function tool therefore appears in both (accepted duplication). Delegating tools stay here too.
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
    def _describe_prompt_functions(fns: list[Any]) -> list[dict[str, Any]]:
        """Describe each prompt/history-processor function as ``{name, signature?, doc?, source?, source_truncated?}``.

        ``inspect.getsource`` returns the full definition including the decorator line (e.g.
        ``@agent.instructions``) -- intended, since it shows how the function is wired.

        AIDEV-NOTE: the captured ``source`` is byte-capped (``_PROMPT_SOURCE_MAX_BYTES``) but NOT
        content-redacted -- a function body is arbitrary code with no allowlistable structure, so there is
        nothing to scrub the way ``_redact_mcp_uri`` scrubs a URL. Conscious tradeoff; a redaction hook
        attaches here.
        """
        described: list[dict[str, Any]] = []
        for fn in fns:
            entry: dict[str, Any] = {"name": getattr(fn, "__name__", None) or "prompt_function"}
            try:
                sig = inspect.signature(fn)
                # Drop default VALUES -- their repr can leak a secret, be huge, or raise a custom
                # ``__repr__``; keep parameter names + annotations. Cap the rendered string like source.
                sig = sig.replace(
                    parameters=[p.replace(default=inspect.Parameter.empty) for p in sig.parameters.values()]
                )
                signature = str(sig)
                if len(signature) <= _PROMPT_SOURCE_MAX_BYTES:
                    entry["signature"] = signature
            except Exception:  # noqa: BLE001 - signature rendering must degrade, not drop the manifest
                pass
            try:
                doc = inspect.getdoc(fn)
            except Exception:  # noqa: BLE001 - a raising __doc__ descriptor must degrade, not drop the manifest
                doc = None
            if doc:
                # ``doc`` rides every agent span; cap it like source so a large docstring can't push the
                # event over the size limit and evict the user's real I/O.
                if len(doc) > _PROMPT_SOURCE_MAX_BYTES:
                    entry["doc_truncated"] = True
                else:
                    entry["doc"] = doc
            try:
                source: Optional[str] = inspect.getsource(fn)
            except (OSError, TypeError):
                source = None
            if source is None or len(source) > _PROMPT_SOURCE_MAX_BYTES:
                # No retrievable source (lambda, REPL-defined, C-implemented) or over the byte budget.
                entry["source_truncated"] = True
            else:
                entry["source"] = source
            described.append(entry)
        return described

    @classmethod
    def _get_history_processors(cls, agent: Any) -> list[dict[str, Any]]:
        """Describe the agent's message-history processors -- its memory / history policy.

        ``agent.history_processors`` is a public list of bare callables (verified 0.8.1 / 1.0.0 / 1.63.0),
        so no ``.function`` unwrap is needed. Order is preserved (semantic): ``[trim, summarize]`` differs
        from ``[summarize, trim]``.
        """
        processors = getattr(agent, "history_processors", None) or []
        fns = _dedupe_by_id([fn for fn in processors if callable(fn)])
        return cls._describe_prompt_functions(fns)

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
        """Build the agent's ``settings`` (a flat additive key); only fields present.

        ``retries`` is the output-validation retry budget (``_max_result_retries``/``_max_output_retries``);
        ``tool_retries`` is the distinct per-tool retry budget (``_max_tool_retries``).
        """
        settings: dict[str, Any] = {}
        # pydantic-ai >1.63.0 renamed ``_max_result_retries`` -> ``_max_output_retries``; read the
        # successor when the old name is absent.
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
        # Omit the "no deps" default -- ``NoneType`` (<2.x) or ``object`` (>=2.x) -- so it isn't noise.
        if isinstance(deps_type, type) and deps_type not in (type(None), object):
            settings["deps_type"] = deps_type.__name__
        return settings

    def _get_agent_output_type(self, agent: Any) -> dict[str, Any]:
        """Build ``output_type`` ``{name, schema?}`` from ``agent.output_type`` (callables go to ``handoffs``)."""
        if not hasattr(agent, "output_type"):
            return {}
        candidates = self._unwrap_output_markers(agent.output_type)
        # Prefer a structured BaseModel; else the first non-callable type, so a union like
        # ``[str, MyTool]`` still names ``str``.
        model_candidate = None
        scalar_candidate = None
        for candidate in candidates:
            if isinstance(candidate, type) and self._is_pydantic_model(candidate):
                model_candidate = candidate
                break
            if scalar_candidate is None and not self._is_output_function(candidate):
                scalar_candidate = candidate
        chosen = model_candidate or scalar_candidate
        if chosen is None:
            return {}
        name = getattr(chosen, "__name__", None) or str(chosen)
        output_type: dict[str, Any] = {"name": name}
        if model_candidate is not None:
            schema = self._bounded_output_schema(model_candidate)
            if schema is not None:
                output_type["schema"] = schema
            else:
                output_type["schema_truncated"] = True
        return output_type

    @staticmethod
    def _bounded_output_schema(model: Any) -> Optional[dict[str, Any]]:
        """Return ``model``'s JSON schema within ``_OUTPUT_SCHEMA_MAX_BYTES``, else ``None`` (name-only fallback)."""
        try:
            schema: dict[str, Any] = model.model_json_schema()
        except Exception:  # noqa: BLE001 - schema generation can raise on exotic models
            return None
        serialized = safe_json(schema)
        if serialized is None or len(serialized) > _OUTPUT_SCHEMA_MAX_BYTES:
            return None
        return schema

    def _get_guardrails(self, agent: Any) -> list[dict[str, Any]]:
        """Describe the agent's output guardrails (``@agent.output_validator`` -> ``agent._output_validators``).

        Each ``OutputValidator`` wraps the user function in ``.function``; describe it like a prompt function
        (name/signature/doc/size-bounded source -- see the AIDEV-NOTE on ``_describe_prompt_functions`` re:
        source is byte-capped but not content-redacted). Order-incidental -> the caller sorts by name.
        """
        validators = getattr(agent, "_output_validators", None) or []
        fns = _dedupe_by_id([getattr(v, "function", v) for v in validators])
        return self._describe_prompt_functions([fn for fn in fns if callable(fn)])

    def _get_tool_transforms(self, agent: Any) -> list[dict[str, Any]]:
        """Describe per-run tool-set rewriters (``prepare_tools`` / ``prepare_output_tools``).

        Each is a single callable on ``agent._prepare_tools`` / ``agent._prepare_output_tools`` (absent on
        agents that set neither); describe it and tag the ``scope`` it applies to.
        """
        transforms: list[dict[str, Any]] = []
        for attr, scope in (("_prepare_tools", "tools"), ("_prepare_output_tools", "output_tools")):
            fn = getattr(agent, attr, None)
            if callable(fn):
                for described in self._describe_prompt_functions([fn]):
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

        AIDEV-NOTE: never read ``label`` -- ``MCPServer.label`` falls back to ``repr(self)``, which embeds
        the connection config (URL userinfo/query secrets, stdio ``command``/``args``), bypassing ``_redact_mcp_uri``.
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
        """List every output-function callable in ``agent.output_type`` as a handoff.

        Entry ``{name, description?, agent?}`` -- ``name`` is the output function, ``agent`` the target it
        delegates to (when statically resolvable).
        """
        if not hasattr(agent, "output_type"):
            return []
        handoffs: list[dict[str, Any]] = []
        for marker, fn in self._iter_output_functions(agent.output_type):
            handoff: dict[str, Any] = {"name": getattr(fn, "__name__", None) or "output_function"}
            description = getattr(marker, "description", None) or getattr(fn, "__doc__", None)
            if description:
                handoff["description"] = description
            agent_name = self._referenced_agent_name(fn)
            if agent_name:
                handoff["agent"] = agent_name
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

    @staticmethod
    def _referenced_agent_name(fn: Any) -> Optional[str]:
        """Return the ``name`` of an ``Agent`` in ``fn``'s globals/closure, else ``None``.

        Sound but incomplete: agents reached via ``ctx.deps``, a registry lookup, or a helper return are not seen.
        """
        try:
            from pydantic_ai import Agent
        except Exception:  # noqa: BLE001
            return None
        code = getattr(fn, "__code__", None)
        if code is None:
            return None
        glbls = getattr(fn, "__globals__", {}) or {}
        cellmap: dict[str, Any] = {}
        for var, cell in zip(code.co_freevars, fn.__closure__ or ()):
            try:
                cellmap[var] = cell.cell_contents
            except ValueError:
                continue
        for name in list(code.co_names) + list(code.co_freevars):
            obj = cellmap[name] if name in cellmap else glbls.get(name)
            if isinstance(obj, Agent):
                # AIDEV-NOTE: emit ONLY the name string, never ``obj`` -- the Agent holds its model client,
                # deps, prompts, and tool closures, so serializing it onto the span would leak credentials/PII.
                return getattr(obj, "name", None) or ""
        return None

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
