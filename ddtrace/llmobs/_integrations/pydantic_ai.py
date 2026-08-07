import functools
import hashlib
import inspect
from typing import Any
from typing import Optional
from typing import Sequence
from typing import get_args
from typing import get_origin
import urllib.parse

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
from ddtrace.llmobs._integrations.agent_manifest import MANIFEST_VERSION
from ddtrace.llmobs._integrations.agent_manifest import is_number
from ddtrace.llmobs._integrations.agent_manifest import put_field
from ddtrace.llmobs._integrations.agent_manifest import wire_schema
from ddtrace.llmobs._integrations.agent_manifest import wire_value
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import safe_json
from ddtrace.trace import Span


log = get_logger(__name__)


# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}

# The display name the integration has always emitted. Kept so the shared schema migration does not
# also silently re-key every existing consumer's framework filter.
FRAMEWORK_NAME = "PydanticAI"

_HTTP_MCP_SCHEMES = frozenset({"http", "https"})
# Legal hostname characters, plus ":" for IPv6. An IDN host drops, which is the safe direction.
_HOSTNAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789.-:")
# How far into a declared generic to look for a model. dict[str, list[Row]] is 2, and a caller can
# nest further, but an output contract that deep is not worth an unbounded walk on every agent span.
_MAX_OUTPUT_TYPE_DEPTH = 4

# AIDEV-NOTE: an ALLOWLIST, not a denylist. Provider passthroughs on model_settings carry credentials
# (extra_headers shipped a live Bearer token on origin/main) and providers keep adding them, so a
# denylist is an open set. Mirrors langgraph's ALLOWED_MODEL_SETTINGS_KEYS. Widening it is a
# security decision.
_ALLOWED_MODEL_SETTINGS_KEYS = frozenset(
    {
        "frequency_penalty",
        "logit_bias",
        "logprobs",
        "max_tokens",
        "parallel_tool_calls",
        "presence_penalty",
        "seed",
        "stop_sequences",
        "temperature",
        "timeout",
        "tool_choice",
        "top_k",
        "top_logprobs",
        "top_p",
    }
)


def _is_flat_scalar_value(value: Any) -> bool:
    """True for a JSON scalar, a flat list of scalars, or a flat mapping of scalars.

    The allowlist protects the key; this protects the value. logit_bias and tool_choice accept a
    caller-supplied mapping, which is an unbounded blob and the same shape the transport passthroughs
    use to carry credentials. A nested structure drops rather than shipping whatever it holds.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(item is None or isinstance(item, (str, int, float, bool)) for item in value)
    if isinstance(value, dict):
        # Numeric values only. The one allowlisted mapping is logit_bias, which is token id to bias,
        # so a string value there is already invalid and is the last way to smuggle one through.
        return all(
            isinstance(key, (str, int)) and isinstance(item, (int, float)) and not isinstance(item, bool)
            for key, item in value.items()
        )
    return False


# Builtin-tool config fields allowed onto the wire, so a newly added secret-bearing field drops by
# default rather than by name. A url is emitted separately and scrubbed; tokens and headers never are.
_BUILTIN_TOOL_CONFIG_FIELDS = frozenset(
    {
        "allowed_domains",
        "allowed_tools",
        "aspect_ratio",
        "background",
        "blocked_domains",
        "description",
        "enable_citations",
        "input_fidelity",
        "max_content_tokens",
        "max_uses",
        "moderation",
        "output_compression",
        "output_format",
        "partial_images",
        "quality",
        "search_context_size",
        "size",
    }
)


def _redact_mcp_uri(raw: Any) -> Optional[str]:
    """Scrub an HTTP MCP URL down to scheme://host with an optional port.

    An allowlist, so a new secret-bearing URL component drops by default rather than by name.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if not text or any(character.isspace() for character in text):
        # Whitespace means this is not a URL. A stdio server's command line lands here, and its first
        # token would otherwise be rebuilt into a host that was never one.
        return None
    try:
        parsed = urllib.parse.urlsplit(text)
        scheme = parsed.scheme.lower()
        if not parsed.netloc and "//" not in text:
            # A scheme-less host:port parses with the host AS the scheme, so re-parse with a leading //
            # to let urlsplit find it. Only when what follows the colon is a port, though: otherwise
            # this is a non-HTTP URI such as stdio:/usr/bin/srv, and rebuilding it would assert an
            # https MCP server that does not exist.
            _, _, after_colon = text.partition(":")
            if scheme and not after_colon.isdigit():
                return None
            parsed = urllib.parse.urlsplit("//" + text)
        elif scheme and scheme not in _HTTP_MCP_SCHEMES:
            return None
        host = parsed.hostname
        port = parsed.port
        username = parsed.username
    except ValueError:
        # urlsplit itself raises, not only .hostname/.port: an NFKC-normalizing netloc, a bad port.
        return None
    if not host:
        return None
    # AIDEV-NOTE: urlsplit ends the authority at the first "/", so a credential containing "/" parses
    # as the host. Drop when an "@" precedes the path without userinfo, or the host is not hostname-like.
    if username is None and "@" in text.split("?")[0].split("#")[0]:
        return None
    if not set(host).issubset(_HOSTNAME_CHARS):
        return None
    if ":" in host:  # IPv6 literal, re-bracket so the rebuilt authority stays parseable
        host = "[{}]".format(host)
    if port is not None:
        host = "{}:{}".format(host, port)
    scheme = parsed.scheme.lower() if parsed.scheme and parsed.scheme.lower() in _HTTP_MCP_SCHEMES else "https"
    return "{}://{}".format(scheme, host)


@functools.lru_cache(maxsize=1)
def _output_marker_classes() -> tuple[type, ...]:
    """Output-marker classes present in this pydantic-ai. A marker absent from the version is skipped."""
    try:
        from pydantic_ai import output as _output
    except Exception:  # noqa: BLE001 - pydantic_ai.output does not exist on very old versions
        return ()
    classes: list[type] = []
    for name in ("ToolOutput", "NativeOutput", "PromptedOutput", "TextOutput"):
        cls = getattr(_output, name, None)
        if isinstance(cls, type):
            classes.append(cls)
    return tuple(classes)


def _iter_agent_tools(agent: Any):
    """Yield (name, tool, fn) for the agent's function tools, de-duped first-wins, across versions."""
    seen: set[str] = set()
    tool_dicts: list[dict[str, Any]] = []
    function_tools = getattr(agent, "_function_tools", None)
    if function_tools:
        tool_dicts.append(function_tools)
    else:
        function_toolset = getattr(agent, "_function_toolset", None)
        user_toolsets: Sequence[Any] = getattr(agent, "_user_toolsets", None) or []
        # Only a FunctionToolset exposes a {name: tool} dict. A custom toolset's .tools may be something
        # else entirely, and is captured as a custom capability instead, so gate on the class and on
        # dict-ness before reading.
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
    """Collapse duplicates by key, default object id, preserving first-seen order.

    Pair call sites pass key=lambda p: id(p[0]) to dedupe (fn, flag) tuples on the function id.
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
    """Best recoverable name for a callable, never a fabricated constant.

    Two distinct unnamed callables have to stay distinguishable in the manifest.
    """
    return getattr(fn, "__name__", None) or getattr(getattr(fn, "func", None), "__name__", None) or type(fn).__name__


def _type_name(candidate: Any) -> str:
    """Readable name for an output-type candidate, including a parameterized generic like list[Fruit]."""
    if get_origin(candidate) is not None:
        return str(candidate)
    return getattr(candidate, "__name__", None) or str(candidate)


def _collect_instructions(agent: Any) -> tuple[list[str], list[tuple[Any, bool]]]:
    """Gather (static_texts, dynamic_resolvers) from an agent's instructions, across versions.

    Version drift: _instructions is a str below 1.63.0 and a mixed list from 1.63.0 on, and dynamic
    resolvers live in _instructions_functions below 1.63.0 but inline in the list after. Read both and
    de-dupe by id. Instructions are rebuilt on every request, so a resolver here is always reevaluated.
    """
    static_texts: list[str] = []
    dynamic: list[tuple[Any, bool]] = []
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
    """Gather (fn, reevaluated) dynamic system-prompt resolvers, present on every supported version.

    Static prompts ship verbatim in agent._system_prompts, read directly by the builder, so only the
    resolver wrappers are collected here. A runner marked dynamic re-runs on each step.
    """
    dynamic: list[tuple[Any, bool]] = []
    for runner in getattr(agent, "_system_prompt_functions", None) or []:
        fn = getattr(runner, "function", runner)
        if callable(fn):
            dynamic.append((fn, bool(getattr(runner, "dynamic", False))))
    return _dedupe_by_id(dynamic, key=lambda p: id(p[0]))


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"
    _running_agents: dict[int, list[int]] = {}  # dictionary mapping agent span ID to tool span ID(s)
    _latest_agent = None  # str representing the span ID of the latest agent that was started
    _run_stream_active = False  # bool indicating if the latest agent span was generated from run_stream

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
        # Manifest last. Each grouping is failure-isolated, but the annotate call above is not, so
        # setting name, input and output first means a manifest problem degrades to a span with no
        # manifest rather than a span with nothing on it.
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
        # str-only for the same reason the manifest guards it: pydantic-ai accepts a non-str
        # description, and the span encoder falls back to repr, which can disclose the object.
        if not isinstance(tool_description, str):
            tool_description = ""

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
        _annotate_llmobs_span_data(span, agent_manifest=self._build_agent_manifest(agent))

    def _build_agent_manifest(self, agent: Any) -> dict[str, Any]:
        """Build the shared agent manifest from a pydantic-ai Agent.

        Sections are built independently so a framework change inside one cannot blank the rest. Only
        declared configuration is read, so the manifest is identical run to run, and a field
        pydantic-ai does not expose is omitted rather than invented.
        """
        manifest: dict[str, Any] = {"manifest_version": MANIFEST_VERSION}
        # The name is a literal, not section.__name__: reading an attribute off the section inside the
        # handler can itself raise, and a second exception there escapes and costs the whole manifest.
        for name, section in (
            ("labels", self._manifest_labels),
            ("instructions", self._manifest_instructions),
            ("model", self._manifest_model),
            ("capabilities", self._manifest_capabilities),
            ("data_contracts", self._manifest_data_contracts),
            ("memory_policies", self._manifest_memory_policies),
            ("guardrails", self._manifest_guardrails),
            ("agent_settings", self._manifest_agent_settings),
        ):
            try:
                manifest.update(section(agent))
            except Exception:
                log.debug("failed to build pydantic_ai agent manifest section %s", name, exc_info=True)
        return manifest

    def _manifest_labels(self, agent: Any) -> dict[str, Any]:
        """Labels that name the agent rather than describing its behaviour.

        These are builder groupings for failure isolation, not schema sections. The manifest is one
        flat document, so no grouping name reaches the wire.
        """
        fields: dict[str, Any] = {"framework": FRAMEWORK_NAME}
        # AIDEV-NOTE: the placeholder is deliberate, per review: a consumer needs something to display
        # for an unnamed agent, and the span name already falls back to the same literal. The cost is
        # that two distinct unnamed agents share a name here, so name must not be treated as identity
        # for versioning. pydantic-ai may also infer a name from the caller's frame, which we cannot
        # detect, so an absent name is not proof the caller left it unset.
        agent_name = getattr(agent, "name", None)
        put_field(fields, "name", agent_name if isinstance(agent_name, str) and agent_name else "PydanticAI Agent")
        # _description is absent across the whole supported range (0.8.1 to 1.63.0) and present by
        # 2.24.0, so on every version CI exercises this key never appears.
        description = getattr(agent, "_description", None)
        put_field(fields, "description", description if isinstance(description, str) else None)
        metadata = getattr(agent, "_metadata", None)
        # metadata accepts a callable from 1.39.0 on, resolved per run. Not captured: it is caller
        # labels rather than behaviour, and nothing downstream versions on it, so a resolver's
        # identity would buy nothing. Recording it needs a key the shared schema does not define.
        put_field(fields, "metadata", wire_value(metadata) if isinstance(metadata, dict) else None)
        return fields

    def _manifest_instructions(self, agent: Any) -> dict[str, Any]:
        """What the agent is told: static text, static system prompts, and dynamic resolvers.

        instructions is a plain string, so a consumer never has to branch on its type. Every resolver
        lands in extra_instructions as {type, name, content}, which is what makes a prompt change
        detectable: a boolean "is dynamic" would say that the text varies but not how.
        """
        fields: dict[str, Any] = {}
        static_texts, dynamic_instructions = _collect_instructions(agent)
        # Newline join to match how pydantic-ai renders a multi-entry instructions list.
        put_field(fields, "instructions", "\n".join(text for text in static_texts if text))
        # pydantic-ai does not validate these; a non-string would ship as a repr with a memory address.
        prompts = [p for p in (getattr(agent, "_system_prompts", None) or ()) if isinstance(p, str)]
        put_field(fields, "system_prompts", prompts)
        extra: list[dict[str, Any]] = []
        for kind, resolvers in (
            ("dynamic_instructions", dynamic_instructions),
            ("dynamic_system_prompt", _collect_dynamic_system_prompts(agent)),
        ):
            for fn, reevaluated in resolvers:
                for described in self._describe_functions([fn]):
                    entry: dict[str, Any] = {"type": kind, "name": described.pop("name", "")}
                    described["reevaluated"] = reevaluated
                    put_field(entry, "content", described)
                    extra.append(entry)
        put_field(fields, "extra_instructions", extra)
        return fields

    def _manifest_model(self, agent: Any) -> dict[str, Any]:
        """The model and the inference params the user set.

        model_settings is filtered through an allowlist rather than copied. agent.model_settings is the
        caller's own dict and routinely carries provider passthroughs that hold credentials.
        """
        fields: dict[str, Any] = {}
        model = getattr(agent, "model", None)
        if isinstance(model, str):
            # With defer_model_check the declared value stays a string like "openai:gpt-4o". Split on
            # the FIRST colon: a bedrock or azure model name contains its own, and rpartition would
            # report "bedrock:anthropic.claude-v1:0" as the model "0".
            _, _, declared_name = model.partition(":")
            put_field(fields, "model", declared_name or model)
        elif model:
            model_name, _ = self._get_model_and_provider(model)
            put_field(fields, "model", model_name)
        settings = getattr(agent, "model_settings", None)
        if isinstance(settings, dict):
            allowed: dict[str, Any] = {}
            for key, value in settings.items():
                if key not in _ALLOWED_MODEL_SETTINGS_KEYS or not _is_flat_scalar_value(value):
                    continue
                # Through put_field, not a direct assignment: wire_value returns None for a value
                # it cannot encode, such as NaN, and a direct assignment would ship that as an
                # explicit null. Absence has to keep meaning "not configured".
                put_field(allowed, key, wire_value(value))
            put_field(fields, "model_settings", allowed)
        return fields

    def _manifest_capabilities(self, agent: Any) -> dict[str, Any]:
        """Function tools stay on their own key for backward compatibility, and also appear in the
        typed capabilities list alongside the powers that are not plain functions.
        """
        fields: dict[str, Any] = {}
        tools = self._get_agent_tools(agent)
        put_field(fields, "tools", tools)
        # AIDEV-NOTE: a prepare hook rewrites or removes tool definitions on every step, so the list
        # above is what was declared, NOT what the model was shown. Recording the hook makes a change
        # to it move the manifest; it does NOT make the tool list correct. An agent that gates a
        # destructive tool behind prepare_tools still lists that tool here. Correcting the list needs
        # the resolved per-run toolset, which is separate work.
        prepared = [
            fn
            for fn in (getattr(agent, "_prepare_tools", None), getattr(agent, "_prepare_output_tools", None))
            if callable(fn)
        ]
        capabilities: list[dict[str, Any]] = [self._as_capability(entry, "tool") for entry in tools]
        for kind, entries in (
            ("mcp", self._get_mcp_servers(agent)),
            ("builtin", self._get_builtin_tools(agent)),
            ("custom", self._get_toolsets(agent)),
            ("tool_preparation", self._describe_functions(prepared)),
        ):
            capabilities.extend(self._as_capability(entry, kind) for entry in entries)
        put_field(fields, "capabilities", capabilities)
        return fields

    def _manifest_data_contracts(self, agent: Any) -> dict[str, Any]:
        """The typed edges. pydantic-ai declares an output type but no input schema, so only output."""
        output = self._get_agent_output_type(agent)
        contract: dict[str, Any] = {}
        put_field(contract, "name", output.get("name"))
        put_field(contract, "schema", wire_schema(output.get("schema")))
        return {"data_contracts": {"output": contract}} if contract else {}

    def _manifest_memory_policies(self, agent: Any) -> dict[str, Any]:
        """The message-history pipeline, order preserved.

        [trim, summarize] is not the same policy as [summarize, trim]. history_processors is gone
        from 2.x, so the key drops out on its own.
        """
        fields: dict[str, Any] = {}
        processors = getattr(agent, "history_processors", None) or []
        history = _dedupe_by_id([fn for fn in processors if callable(fn)])
        put_field(fields, "memory_policies", [self._as_named_content(d) for d in self._describe_functions(history)])
        return fields

    def _manifest_guardrails(self, agent: Any) -> dict[str, Any]:
        """Output validators, order preserved for the same reason as the history pipeline."""
        fields: dict[str, Any] = {}
        validators = getattr(agent, "_output_validators", None) or []
        fns = _dedupe_by_id([getattr(v, "function", v) for v in validators])
        checks = self._describe_functions([fn for fn in fns if callable(fn)])
        put_field(fields, "guardrails", [self._as_named_content(d) for d in checks])
        return fields

    def _manifest_agent_settings(self, agent: Any) -> dict[str, Any]:
        """Knobs that govern the loop across calls, as opposed to model params which tune one call.

        retries is the output-validation budget, tool_retries the per-tool one. Separate in pydantic-ai,
        so Agent(retries=3, output_retries=2) is retries 2 with tool_retries 3, not one number.
        """
        settings: dict[str, Any] = {}
        # 1.107.1 renamed _max_result_retries to _max_output_retries, so fall back to the successor.
        retries = getattr(agent, "_max_result_retries", None)
        if not is_number(retries):
            retries = getattr(agent, "_max_output_retries", None)
        for name, value in (
            ("retries", retries),
            ("tool_retries", getattr(agent, "_max_tool_retries", None)),
            ("tool_timeout", getattr(agent, "_tool_timeout", None)),
            # The parameter is not retained; it is normalized into a limiter at construction, and that
            # limiter is None when unset, which keeps "unset" distinct from a real value.
            ("max_concurrency", getattr(getattr(agent, "_concurrency_limiter", None), "max_running", None)),
        ):
            if is_number(value):
                settings[name] = value
        end_strategy = getattr(agent, "end_strategy", None)
        if isinstance(end_strategy, str):
            put_field(settings, "end_strategy", end_strategy)
        deps_type = getattr(agent, "_deps_type", None)
        # Omit the "no deps" default, NoneType below 2.x and object from 2.x on, so it is not noise.
        if isinstance(deps_type, type) and deps_type not in (type(None), object):
            put_field(settings, "deps_type", deps_type.__name__)
        return {"agent_settings": settings} if settings else {}

    @staticmethod
    def _as_capability(entry: dict[str, Any], kind: str) -> dict[str, Any]:
        """Reshape an extractor entry into the shared {name, type, description?, content?} shape."""
        capability: dict[str, Any] = {"name": entry.get("name", ""), "type": kind}
        put_field(capability, "description", entry.get("description"))
        put_field(capability, "content", {k: v for k, v in entry.items() if k not in ("name", "description")})
        return capability

    @staticmethod
    def _as_named_content(described: dict[str, Any]) -> dict[str, Any]:
        """Reshape a {name, source_hash?} descriptor into the shared {name, content?} entry shape."""
        entry: dict[str, Any] = {"name": described.get("name", "")}
        put_field(entry, "content", {k: v for k, v in described.items() if k != "name"})
        return entry

    def _get_agent_tools(self, agent: Any) -> list[dict[str, Any]]:
        """Function tools as {name, description?, parameters?}, each exactly once.

        For pydantic-ai below 0.4.4 tools live on the agent's _function_tools. From 0.4.4 on they live
        on _function_toolset and on any user-supplied FunctionToolset in _user_toolsets.
        """
        tools: list[dict[str, Any]] = []
        for tool_name, tool_instance, _fn in _iter_agent_tools(agent):
            entry: dict[str, Any] = {"name": tool_name if isinstance(tool_name, str) else str(tool_name)}
            # AIDEV-NOTE: str-only, because Tool(fn, description=<object>) is accepted by pydantic-ai
            # and nothing downstream coerces it. safe_json falls back to repr for a value it cannot
            # encode, so an object here is printed onto the wire, and a repr can carry credentials.
            description = getattr(tool_instance, "description", None)
            put_field(entry, "description", description if isinstance(description, str) else None)
            put_field(entry, "parameters", self._tool_parameters(tool_instance))
            tools.append(entry)
        return tools

    @staticmethod
    def _tool_parameters(tool_instance: Any) -> dict[str, dict[str, Any]]:
        """Extract {param: {type?, required?}} from a tool's function_schema.json_schema."""
        function_schema = getattr(tool_instance, "function_schema", {})
        json_schema = getattr(function_schema, "json_schema", {})
        if not isinstance(json_schema, dict):
            return {}
        required = json_schema.get("required")
        required_params = {str(param) for param in required} if isinstance(required, (list, tuple, set)) else set()
        properties = json_schema.get("properties")
        if not isinstance(properties, dict):
            return {}
        parameters: dict[str, dict[str, Any]] = {}
        for param, schema in properties.items():
            # AIDEV-NOTE: keys coerced to str. Tool.from_schema takes a caller-supplied json_schema, so
            # a non-str key reaches here, and safe_json sorts keys: comparing an int to a str raises and
            # the encoder returns None, dropping the whole batched payload rather than this one span.
            param_dict: dict[str, Any] = {}
            if isinstance(schema, dict):
                put_field(param_dict, "type", wire_value(schema.get("type")))
            if str(param) in required_params:
                param_dict["required"] = True
            parameters[str(param)] = param_dict
        return parameters

    @staticmethod
    def _describe_functions(fns: list[Any]) -> list[dict[str, Any]]:
        """Describe each function as {name, source_hash?}, the shared descriptor shape.

        AIDEV-NOTE: hash the source instead of shipping it, so a change is detectable without leaking
        code or the secrets a body can hold. signature and doc are dropped for the same reason. The hash
        covers the decorator and indentation, so it is a change signal, not a semantic fingerprint.
        """
        described: list[dict[str, Any]] = []
        for fn in fns:
            entry: dict[str, Any] = {"name": _callable_name(fn)}
            try:
                source: Optional[str] = inspect.getsource(fn)
            except (OSError, TypeError):
                # No retrievable source (a lambda, a REPL definition, a C function), so name only.
                source = None
            if source is not None:
                entry["source_hash"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
            described.append(entry)
        return described

    def _get_builtin_tools(self, agent: Any) -> list[dict[str, Any]]:
        """Provider-side builtin tools as {name, config?}, with per-tool config allowlisted.

        Source is agent._builtin_tools, readable up to 1.63.0. At 1.107.1 the parameter is accepted but
        no longer retained, and at 2.x it is gone entirely, so the field drops out on its own.
        """
        entries: list[dict[str, Any]] = []
        for tool in getattr(agent, "_builtin_tools", None) or []:
            name = getattr(tool, "kind", None) or type(tool).__name__
            if not name:
                continue
            entry: dict[str, Any] = {"name": name}
            config: dict[str, Any] = {}
            for field in sorted(_BUILTIN_TOOL_CONFIG_FIELDS):
                put_field(config, field, wire_value(getattr(tool, field, None)))
            put_field(config, "uri", _redact_mcp_uri(getattr(tool, "url", None)))
            put_field(entry, "config", config)
            entries.append(entry)
        return entries

    def _get_toolsets(self, agent: Any) -> list[dict[str, Any]]:
        """Toolsets that are neither function tools nor MCP servers, so none is silently dropped.

        A dynamic toolset is a caller factory re-resolved per run or per step, so only the factory's
        identity is static. What it returns is per-invocation and is not a property of the agent.
        """
        mcp_classes = self._mcp_server_classes()
        fn_cls = self._function_toolset_cls()
        entries: list[dict[str, Any]] = []
        for toolset in getattr(agent, "_user_toolsets", None) or []:
            if mcp_classes and isinstance(toolset, mcp_classes):
                continue
            if fn_cls is not None and isinstance(toolset, fn_cls):
                continue
            entries.append({"name": self._toolset_name(toolset)})
        for toolset in getattr(agent, "_dynamic_toolsets", None) or []:
            fn = getattr(toolset, "toolset_func", None)
            described = self._describe_functions([fn])[0] if callable(fn) else {"name": self._toolset_name(toolset)}
            described["dynamic"] = True
            entries.append(described)
        return entries

    def _get_agent_output_type(self, agent: Any) -> dict[str, Any]:
        """The output contract as {name, schema?}. An output function is not a declared type, so it is skipped.

        A union such as [Fruit, Vehicle] is captured in full, so changing any alternative is reflected.
        """
        if not hasattr(agent, "output_type"):
            return {}
        candidates = [c for c in self._unwrap_output_markers(agent.output_type) if not self._is_output_function(c)]
        if not candidates:
            return {}
        output_type: dict[str, Any] = {"name": " | ".join(_type_name(c) for c in candidates)}
        # A container such as list[Row] is not itself a model, so testing the candidate alone drops the
        # schema and leaves the contract as a bare name. pydantic-ai validates the container all the
        # same, so a field change inside Row has to move the schema.
        if any(self._references_pydantic_model(c) for c in candidates):
            schema = self._output_schema(candidates)
            if schema is not None:
                output_type["schema"] = schema
        return output_type

    def _references_pydantic_model(self, candidate: Any, depth: int = 0) -> bool:
        """True if candidate is a pydantic model or parameterizes one, such as list[Row]."""
        if self._is_pydantic_model(candidate):
            return True
        if depth >= _MAX_OUTPUT_TYPE_DEPTH:
            return False
        return any(self._references_pydantic_model(arg, depth + 1) for arg in get_args(candidate))

    @staticmethod
    def _output_schema(candidates: list[Any]) -> Optional[dict[str, Any]]:
        """JSON schema for one pydantic model or a union of several, or None on generation failure.

        A single model must use model_json_schema, which inlines properties; the TypeAdapter union form
        wraps members in $ref and $defs, so it is reserved for a real multi-member union.
        """
        try:
            from pydantic import TypeAdapter

            if len(candidates) == 1:
                candidate = candidates[0]
                # Duck-typed rather than issubclass(BaseModel): a bare model carries the method and a
                # parameterized generic does not, which is exactly the split, and it keeps the narrowing
                # honest for the type checker.
                model_json_schema = getattr(candidate, "model_json_schema", None)
                if callable(model_json_schema):
                    schema: dict[str, Any] = model_json_schema()
                else:
                    # A parameterized generic has no model_json_schema, so the adapter is the only way
                    # to reach the member model's fields. It emits $ref plus $defs, which is fine here:
                    # the container is the contract, so the indirection is the shape.
                    schema = TypeAdapter(candidate).json_schema()
            else:
                from typing import Union

                schema = TypeAdapter(Union[tuple(candidates)]).json_schema()
        except Exception:  # noqa: BLE001 - schema and union generation can raise on exotic models
            return None
        return schema

    @staticmethod
    def _mcp_server_classes() -> tuple[type, ...]:
        """Every MCP class this pydantic-ai defines, for isinstance filtering. Empty when MCP is absent.

        Every present name is matched, not the first found: at 1.107.x MCPServer and MCPToolset both
        exist as unrelated AbstractToolset subclasses, so matching one filed the other as a plain toolset.
        """
        try:
            import pydantic_ai.mcp as mcp_module
        except Exception:  # noqa: BLE001 - the optional mcp extra may not be installed
            return ()
        classes: list[type] = []
        for name in ("MCPServer", "MCPToolset"):
            candidate = getattr(mcp_module, name, None)
            if isinstance(candidate, type):
                classes.append(candidate)
        return tuple(classes)

    @staticmethod
    def _function_toolset_cls() -> Optional[type]:
        """FunctionToolset for isinstance filtering, or None, in which case nothing is filtered out."""
        try:
            from pydantic_ai.toolsets import FunctionToolset
        except Exception:  # noqa: BLE001 - the toolset module layout varies by version
            return None
        fn_cls: type = FunctionToolset
        return fn_cls

    @staticmethod
    def _toolset_name(toolset: Any) -> str:
        """Toolset or MCP server name: the id the user set, else the class name.

        AIDEV-NOTE: never read label. Without an id it falls back to repr(self), leaking the very
        connection config _redact_mcp_uri exists to strip. True on every supported version.
        """
        try:
            toolset_id = getattr(toolset, "id", None)
        except Exception:  # noqa: BLE001 - id is a property on some toolsets and may raise
            toolset_id = None
        # Require a real string: an id can be any object on a custom toolset.
        return toolset_id if isinstance(toolset_id, str) and toolset_id else type(toolset).__name__

    def _get_mcp_servers(self, agent: Any) -> list[dict[str, Any]]:
        """MCP servers as {name, uri?}. Only an HTTP url is emitted, and only scrubbed.

        A stdio server has no url, so nothing is emitted for it: its command and args can carry secrets.
        On 2.x the transport moved inside a client object, so the uri is absent rather than dug out.
        """
        servers: list[dict[str, Any]] = []
        mcp_classes = self._mcp_server_classes()
        if not mcp_classes:
            return servers
        for toolset in getattr(agent, "_user_toolsets", None) or []:
            if not isinstance(toolset, mcp_classes):
                continue
            entry: dict[str, Any] = {"name": self._toolset_name(toolset)}
            put_field(entry, "uri", _redact_mcp_uri(getattr(toolset, "url", None)))
            servers.append(entry)
        return servers

    # The wrapped target lives under a different attr per marker class, so read all three.
    _OUTPUT_MARKER_ATTRS = ("output", "outputs", "output_function")

    @classmethod
    def _marker_target(cls, item: Any) -> tuple[Any, Any]:
        """Return (marker, inner) if item is an output marker, else (None, item).

        The isinstance gate is load-bearing: the wrapper attr names are not exclusive to marker classes,
        so a plain dataclass, NamedTuple or Enum used as an output_type could carry a same-named member.
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
        """Flatten agent.output_type into candidate types: unwrap markers and expand unions."""
        candidates: list[Any] = []
        for item in output_type if isinstance(output_type, (list, tuple)) else [output_type]:
            _, inner = cls._marker_target(item)
            candidates.extend(inner if isinstance(inner, (list, tuple)) else [inner])
        return candidates

    @staticmethod
    def _is_pydantic_model(candidate: Any) -> bool:
        """True if candidate is a pydantic.BaseModel subclass, which means it yields a JSON schema."""
        from pydantic import BaseModel

        return isinstance(candidate, type) and issubclass(candidate, BaseModel)

    @staticmethod
    def _is_output_function(candidate: Any) -> bool:
        """Callable but not a class.

        A parameterized generic such as list[Fruit] is callable and is not an instance of type, so
        without the get_origin check it reads as a function and the real output contract is lost.
        """
        if get_origin(candidate) is not None:
            return False
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
