from collections.abc import Iterable
from collections.abc import Sequence
from typing import Any
from typing import Optional
from typing import Union
from typing import cast
from weakref import WeakKeyDictionary

from ddtrace.contrib.internal.langgraph._constants import ASTREAM_OUTPUT
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import _get_attr
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import format_langchain_io
from ddtrace.llmobs._memory import _as_float
from ddtrace.llmobs._memory import build_memory_annotation
from ddtrace.llmobs._memory import hash_identifier
from ddtrace.llmobs._memory import resolve_memory_span_kind
from ddtrace.llmobs._span import _LLMObsSpan
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_span_links
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import _SpanLink


logger = get_logger(__name__)


PREGEL_PUSH = "__pregel_push"  # represents a task queued up by a `Send` command
PREGEL_TASKS = "__pregel_tasks"  # name of ephemeral channel that pregel `Send` commands write to

_LANGGRAPH_STORE_ID_CTX_KEY = "langgraph.store_id"
_STORE_OPERATION_MAP: dict[str, str] = {
    "store.get": "get_memory",
    "store.aget": "get_memory",
    "store.search": "search_memory",
    "store.asearch": "search_memory",
    "store.put": "upsert_memory",
    "store.aput": "upsert_memory",
    "store.delete": "delete_memory",
    "store.adelete": "delete_memory",
}
_NAMESPACE_SCOPE_MAP: dict[str, str] = {
    "users": "user",
    "user": "user",
    "sessions": "session",
    "session": "session",
    "orgs": "org",
    "org": "org",
    "agents": "agent",
    "agent": "agent",
    "global": "global",
}

ALLOWED_MODEL_SETTINGS_KEYS = [
    "max_tokens",
    "temperature",
    "top_p",
    "top_k",
    "frequency_penalty",
    "presence_penalty",
    "stop",
    "n",
    "logprobs",
    "echo",
    "logit_bias",
]


class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"
    _graph_nodes_for_graph_by_task_id: WeakKeyDictionary[_LLMObsSpan, dict[str, Any]] = WeakKeyDictionary()
    _agent_manifests: WeakKeyDictionary[Any, dict[str, Any]] = WeakKeyDictionary()
    _graph_spans_to_graph_instances: WeakKeyDictionary[_LLMObsSpan, Any] = WeakKeyDictionary()

    def _set_base_span_tags(self, span: _LLMObsSpan, **kwargs: Any) -> None:
        """Nothing to do: LangGraph spans carry no provider or model tags."""

    def _llmobs_span_kind(self, operation_id: str, span: _LLMObsSpan, **kwargs: Any) -> Optional[str]:
        op = kwargs.get("operation", "")
        # Graph spans are agents, known at start for agent attribution.
        if op == "graph":
            return "agent"
        mem_op = _STORE_OPERATION_MAP.get(op)
        if mem_op is not None:
            has_parent = get_llmobs_parent_id(span) != ROOT_PARENT_ID
            return resolve_memory_span_kind(mem_op, has_parent=has_parent)
        return super()._llmobs_span_kind(operation_id, span, **kwargs)

    def _on_request_span_started(
        self,
        span: _LLMObsSpan,
        instance: Any = None,
        operation: str = "",
        request_kwargs: Optional[dict[str, Any]] = None,
    ) -> None:
        if operation in _STORE_OPERATION_MAP:
            if instance is not None:
                store_id = getattr(instance, "name", None) or instance.__class__.__name__
                span._set_ctx_item(_LANGGRAPH_STORE_ID_CTX_KEY, str(store_id))
            return
        if operation != "graph" or instance is None:
            return
        self._graph_spans_to_graph_instances[span] = instance

        # Stamp the agent name at start so children resolve the correct pagent_name: the LLMObs name is the
        # graph's short name, while the APM span name is the long composed one.
        if span.span_type == SpanTypes.LLM:
            agent_name = getattr(instance, "name", None)
            if agent_name:
                _annotate_llmobs_span_data(span, name=agent_name)

    def _llmobs_set_tags(
        self,
        span: _LLMObsSpan,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # oneof graph, node, store.*
    ) -> None:
        if not self.llmobs_enabled:
            return
        if operation in _STORE_OPERATION_MAP:
            self._llmobs_set_store_tags(span, args, kwargs, response, operation)
            return

        inputs = get_argument_value(args, kwargs, 0, "input")
        config = get_argument_value(args, kwargs, 1, "config", optional=True) or {}
        metadata = _get_attr(config, "metadata", {})
        instance_id = metadata.get("langgraph_checkpoint_ns", "").split(":")[-1]
        is_subgraph = config.get("metadata", {}).get("_dd.subgraph", False)

        invoked_node = (
            self._get_node_metadata_from_span(span, instance_id) if operation == "node" or is_subgraph else {}
        )

        span_links: list[_SpanLink] = [_default_span_link(span)]
        invoked_node_span_links: list[_SpanLink] = invoked_node.get("span_links") or []
        if invoked_node_span_links:
            span_links = invoked_node_span_links
        current_span_links: list[_SpanLink] = get_llmobs_span_links(span) or []

        def maybe_format_langchain_io(messages: Any) -> Any:
            if messages is None:
                return None
            return format_langchain_io(messages)

        agent_manifest = None
        span_kind = "task"
        if operation == "graph":
            span_kind = "agent"
            agent = self._graph_spans_to_graph_instances[span]
            agent_manifest = self._get_agent_manifest(agent, args, config)

        _annotate_llmobs_span_data(
            span,
            kind=span_kind,
            input_value=format_langchain_io(inputs),
            output_value=maybe_format_langchain_io(response)
            or maybe_format_langchain_io(span._get_ctx_item(ASTREAM_OUTPUT)),
            name=invoked_node.get("name") or kwargs.get("name", span.name),
            span_links=current_span_links + span_links,
            agent_manifest=agent_manifest,
        )

    def _llmobs_set_store_tags(
        self,
        span: _LLMObsSpan,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any],
        operation: str,
    ) -> None:
        mem_op = _STORE_OPERATION_MAP[operation]
        method_name = operation.split(".", 1)[1]
        has_parent = get_llmobs_parent_id(span) != ROOT_PARENT_ID
        span_kind = resolve_memory_span_kind(mem_op, has_parent=has_parent)

        namespace_arg = (
            args[0]
            if len(args) > 0
            else (kwargs.get("namespace") if "namespace" in kwargs else kwargs.get("namespace_prefix"))
        )
        namespace_str, scope, principal, tenant_id = _parse_store_namespace(namespace_arg)
        store_id = span._get_ctx_item(_LANGGRAPH_STORE_ID_CTX_KEY)

        memory_input: dict[str, Any] = {
            "operation": mem_op,
            "provider": "langgraph",
            "detection_source": "explicit",
        }
        if store_id:
            memory_input["store_id"] = str(store_id)
        if scope:
            memory_input["scope"] = scope
        if namespace_str:
            memory_input["namespace"] = namespace_str
        if principal:
            memory_input["principal"] = principal
        if tenant_id:
            memory_input["tenant_id"] = tenant_id

        explicit_input: Any = None
        if mem_op == "get_memory":
            key = args[1] if len(args) > 1 else kwargs.get("key")
            if key is not None:
                memory_input["query"] = str(key)
            if not span.error:
                if response is None:
                    memory_input["records"] = []
                else:
                    fallback_key = str(key) if key is not None else None
                    memory_input["records"] = [_extract_store_item_record(response, fallback_key=fallback_key)]
        elif mem_op == "search_memory":
            query = kwargs.get("query")
            if query is None and len(args) > 1 and isinstance(args[1], str):
                query = args[1]
            if query is not None:
                memory_input["query"] = str(query)
            if (
                not span.error
                and response is not None
                and isinstance(response, Sequence)
                and not isinstance(response, (str, bytes, bytearray))
            ):
                memory_input["records"] = [_extract_store_item_record(item) for item in response]
        elif mem_op == "upsert_memory":
            key = args[1] if len(args) > 1 else kwargs.get("key")
            value = args[2] if len(args) > 2 else kwargs.get("value")
            if key is not None and str(key):
                explicit_input = {"key": str(key)}
            if not span.error:
                val_text = (
                    value
                    if isinstance(value, str)
                    else (safe_json(value, ensure_ascii=False) or str(value) if value is not None else "")
                )
                record: dict[str, Any] = {"text": val_text}
                if key is not None and str(key):
                    record["id"] = str(key)
                memory_input["records"] = [record]
                if value is not None:
                    memory_input["post_write_bytes"] = len(val_text.encode("utf-8"))
        elif mem_op == "delete_memory":
            key = args[1] if len(args) > 1 else kwargs.get("key")
            if key is not None and str(key):
                explicit_input = {"key": str(key)}
            if not span.error:
                if key is not None and str(key):
                    memory_input["record_ids"] = [str(key)]
                memory_input["record_count"] = 1 if key is not None else 0
                memory_input["write_outcome"] = "delete"

        mem_meta, mem_tags, mem_metrics, mem_input, mem_output = build_memory_annotation(memory_input)
        if span_kind == "retrieval":
            _annotate_llmobs_span_data(
                span,
                kind=span_kind,
                name=f"langgraph.store.{method_name}",
                metadata={"memory": mem_meta},
                tags=mem_tags,
                metrics=mem_metrics or None,
                input_value=mem_input,
                output_documents=mem_output or None,
            )
        else:
            _annotate_llmobs_span_data(
                span,
                kind=span_kind,
                name=f"langgraph.store.{method_name}",
                metadata={"memory": mem_meta},
                tags=mem_tags,
                metrics=mem_metrics or None,
                input_value=explicit_input if explicit_input is not None else mem_input,
                output_value=mem_output,
            )

    def _get_agent_manifest(self, agent: Any, args: Any, config: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Gets the agent manifest for a given agent at the end of its execution."""
        if agent is None:
            return None

        agent_manifest = self._agent_manifests.get(agent)
        if agent_manifest is None:
            tools = _get_tools_from_graph(agent)
            agent_manifest = {"name": agent.name or "LangGraph", "tools": tools}
            self._agent_manifests[agent] = agent_manifest

        if "framework" not in agent_manifest:
            agent_manifest["framework"] = "LangGraph"
        if "max_iterations" not in agent_manifest:
            agent_manifest["max_iterations"] = _get_attr(config, "recursion_limit", 25)

        if (
            "dependencies" not in agent_manifest
            # The shared LLMObs subscriber passes the request args as a list.
            and isinstance(args, (list, tuple))
            and len(args) > 0
            and isinstance(args[0], dict)
        ):
            agent_manifest["dependencies"] = list(args[0].keys())

        return agent_manifest

    def _get_node_metadata_from_span(self, span: _LLMObsSpan, instance_id: str) -> dict[str, Any]:
        """
        Get the node metadata for a given span and its node instance id.
        Additionally, set the span and trace ids on its metadata should another node in a later
        tick of the graph need to be linked to this node.
        """
        parent_span = _get_nearest_llmobs_ancestor(span)
        invoked_node = (
            self._graph_nodes_for_graph_by_task_id.setdefault(parent_span, {}).setdefault(instance_id, {})
            if parent_span
            else {}
        )
        invoked_node["span"] = {"trace_id": format_trace_id(span.trace_id), "span_id": str(span.span_id)}
        return invoked_node

    def llmobs_handle_agent_manifest(self, agent: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        """
        Handles the agent manifest for a given react agent (defined through `langgraph.prebuilt.create_react_agent`),
        and caches it for use when tagging the graph/agent span in the `_get_agent_manifest`.
        """
        if not self.llmobs_enabled:
            return

        model = get_argument_value(
            args, kwargs, 0, "model", True
        )  # required parameter on the langgraph side, but optional should that ever change
        model_name, model_provider, model_settings = _get_model_info(model)

        agent_tools: list[Any] = (
            get_argument_value(args, kwargs, 1, "tools", True) or []
        )  # required parameter on the langgraph side, but optional should that ever change
        tools = _get_tools_from_react_agent(agent_tools)

        system_prompt: Optional[str] = _get_system_prompt_from_react_agent(kwargs.get("prompt"))
        name: Optional[str] = kwargs.get("name")

        agent_manifest: dict[str, Any] = {}

        if model_name:
            agent_manifest["model"] = model_name
        if model_provider:
            agent_manifest["model_provider"] = model_provider
        if model_settings:
            agent_manifest["model_settings"] = model_settings
        if tools:
            agent_manifest["tools"] = tools
        if system_prompt:
            agent_manifest["instructions"] = system_prompt
        if name:
            agent_manifest["name"] = name

        self._agent_manifests[agent] = agent_manifest

    def llmobs_handle_pregel_loop_tick(
        self,
        finished_tasks: dict[str, Any],
        next_tasks: dict[str, Any],
        more_tasks: bool,
        is_subgraph_node: bool = False,
    ) -> None:
        """
        Compute incoming and outgoing span links between finished tasks and queued tasks in the graph.
        """
        try:
            if not self.llmobs_enabled:
                return
            graph_span = (
                LLMObs._instance._current_span()
            )  # we're running between nodes, so the current span should be the pregel graph
            if graph_span is None:
                return

            if not more_tasks:
                self._handle_finished_graph(graph_span, finished_tasks, is_subgraph_node)
                return

            self._handle_intermediary_graph_tick(graph_span, next_tasks, finished_tasks)
        except Exception as e:
            logger.debug("Error in LangGraph span linking operation", exc_info=e)

    def _handle_finished_graph(
        self, graph_span: _LLMObsSpan, finished_tasks: dict[str, Any], is_subgraph_node: bool
    ) -> None:
        """Create the span links for a finished pregel graph from all finished tasks as the graph span's outputs.
        Generate the output-to-output span links for the last nodes in a pregel graph.
        If the graph isn't a subgraph, add a span link from the graph span to the calling LLMObs parent span.
        Note: is_subgraph_node denotes whether the graph is a subgraph node,
         not whether it is a standalone graph (called internally during a node execution).
        """
        graph_caller_span = _get_nearest_llmobs_ancestor(graph_span) if graph_span else None
        output_span_links: list[_SpanLink] = [
            _SpanLink(
                span_id=self._graph_nodes_for_graph_by_task_id[graph_span][task_id]["span"]["span_id"],
                trace_id=self._graph_nodes_for_graph_by_task_id[graph_span][task_id]["span"]["trace_id"],
                attributes={"from": "output", "to": "output"},
            )
            for task_id in finished_tasks.keys()
        ]
        graph_span_span_links: list[_SpanLink] = get_llmobs_span_links(graph_span) or []
        _annotate_llmobs_span_data(graph_span, span_links=graph_span_span_links + output_span_links)
        if graph_caller_span is not None and not is_subgraph_node:
            graph_caller_span_links: list[_SpanLink] = get_llmobs_span_links(graph_caller_span) or []
            span_links: list[_SpanLink] = [
                _SpanLink(
                    span_id=str(graph_span.span_id) or "undefined",
                    trace_id=format_trace_id(graph_span.trace_id),
                    attributes={"from": "output", "to": "output"},
                )
            ]
            _annotate_llmobs_span_data(graph_caller_span, span_links=graph_caller_span_links + span_links)

        return

    def _handle_intermediary_graph_tick(
        self, graph_span: _LLMObsSpan, next_tasks: dict[str, Any], finished_tasks: dict[str, Any]
    ) -> None:
        """
        Handle graph ticks that aren't at the end of the graph execution. Link all next tasks to their trigger tasks
        from the dict of finished tasks. Any finished tasks that do not trigger any queued tasks should be
        linked as output --> output links for the outer graph span.
        """
        task_trigger_channels_to_finished_tasks = _map_channel_writes_to_finished_tasks_ids(finished_tasks)

        used_finished_task_ids: set[Optional[str]] = set()
        for task_id, task in next_tasks.items():
            queued_node = self._graph_nodes_for_graph_by_task_id.setdefault(graph_span, {}).setdefault(task_id, {})
            queued_node["name"] = _get_attr(task, "name", "")

            trigger_ids = self._link_task_to_triggers(
                task, queued_node, graph_span, task_trigger_channels_to_finished_tasks
            )
            used_finished_task_ids.update(trigger_ids)

        self._link_standalone_terminal_tasks(graph_span, finished_tasks, used_finished_task_ids)

    def _link_task_to_triggers(
        self,
        task: Any,
        queued_node: dict[str, Any],
        graph_span: _LLMObsSpan,
        task_trigger_channels_to_finished_tasks: dict[str, list[Union[str, tuple[str, str]]]],
    ) -> list[Optional[str]]:
        """
        Create the span links for a queued task from its triggering trigger tasks.

        Returns the finished task ids used as trigger tasks.
        """
        trigger_ids = _get_trigger_ids_from_finished_tasks(task, task_trigger_channels_to_finished_tasks)

        for node_id in trigger_ids:
            if node_id is None:
                continue

            trigger_node_span = self._graph_nodes_for_graph_by_task_id.get(graph_span, {}).get(node_id, {}).get("span")
            if not trigger_node_span:
                continue

            span_link = _SpanLink(
                span_id=trigger_node_span.get("span_id", ""),
                trace_id=trigger_node_span.get("trace_id", ""),
                attributes={"from": "output", "to": "input"},
            )
            span_links: list[_SpanLink] = queued_node.setdefault("span_links", [])
            span_links.append(span_link)

        return trigger_ids

    def _link_standalone_terminal_tasks(
        self, graph_span: _LLMObsSpan, finished_tasks: dict[str, Any], used_finished_tasks_ids: set[Optional[str]]
    ) -> None:
        """
        Default handler that links any finished tasks not used as triggers for queued tasks to the outer graph span.
        """
        standalone_terminal_task_ids = set(finished_tasks.keys()) - used_finished_tasks_ids
        graph_span_links: list[_SpanLink] = get_llmobs_span_links(graph_span) or []
        for finished_task_id in standalone_terminal_task_ids:
            node = self._graph_nodes_for_graph_by_task_id.get(graph_span, {}).get(finished_task_id)
            if node is None:
                continue

            span = node.get("span")
            if span is None:
                continue

            graph_span_links.append(
                _SpanLink(
                    span_id=span.get("span_id", ""),
                    trace_id=span.get("trace_id", ""),
                    attributes={"from": "output", "to": "output"},
                )
            )
        _annotate_llmobs_span_data(graph_span, span_links=graph_span_links)


def _get_model_info(model: Any) -> tuple[Optional[str], Optional[str], dict[str, Any]]:
    """Get the model name, provider, and settings from a langchain llm"""
    if isinstance(model, str):
        # something like "openai:gpt-4"
        model_provider_str, model_name_str = model.split(":", maxsplit=1)
        return model_name_str, model_provider_str, {}

    model_name = _get_attr(model, "model_name", None)
    model_provider = _get_model_provider(model)
    model_settings = _get_model_settings(model)
    return model_name, model_provider, model_settings


def _get_model_provider(model: Any) -> Optional[str]:
    """Get the model provider from a langchain llm"""
    model_provider_info_fn = _get_attr(model, "_get_ls_params", None)
    if model_provider_info_fn is None or not callable(model_provider_info_fn):
        return None

    model_provider_info: dict[str, Any] = model_provider_info_fn()
    model_provider: Optional[str] = model_provider_info.get("ls_provider", None)
    return model_provider


def _get_model_settings(model: Any) -> dict[str, Any]:
    """Get the model settings from a langchain llm"""
    model_dict_fn = _get_attr(model, "dict", None)
    if model_dict_fn is None or not callable(model_dict_fn):
        return {}

    model_dict: dict[str, Any] = model.dict()
    return {key: value for key, value in model_dict.items() if key in ALLOWED_MODEL_SETTINGS_KEYS and value}


def _get_system_prompt_from_react_agent(system_prompt: Any) -> Optional[str]:
    """
    Get the system prompt from a react agent.

    The system prompt can be:
    - a string
    - a dict with a "content" key
    - a Callable that returns a string or dict

    In the case of a Callable (which is dynamic as a function of state and config), we end up returning None.
    """
    if system_prompt is None:
        return None

    if isinstance(system_prompt, str):
        return system_prompt

    content: Optional[str] = _get_attr(system_prompt, "content", None)
    return content


def _get_tools_from_react_agent(tools: Any) -> Optional[list[dict[str, Any]]]:
    """
    Get the tools for the agent manifest passed into the react agent.

    Tools can be:
    - a ToolNode
    - a list of BaseTools (langchain tools)
    - a list of Callables
    - a list of dicts

    In the case of a Callable (which is dynamic as a function of state and config), we end up returning None.
    """
    if _is_tool_node(tools):
        tools_by_name: dict[str, Any] = _get_attr(tools, "tools_by_name", {})
        tools = list(tools_by_name.values())

    return _extract_tools(tools)


def _get_tool_repr_from_langchain_base_tool(tool: Any) -> Optional[dict[str, Any]]:
    """Get the tool representation from a langchain base tool"""
    if tool is None or isinstance(tool, dict):
        return None

    return {
        "name": _get_attr(tool, "name", ""),
        "description": _get_attr(tool, "description", ""),
        "parameters": _get_attr(tool, "args", {}),
    }


def _is_tool_node(maybe_tool_node: Any) -> bool:
    """Check if a node is a tool node without a specific instance check"""
    return _get_attr(maybe_tool_node, "tools_by_name", None) is not None


def _get_tools_from_graph(agent: Any) -> list[dict[str, Any]]:
    """Get the tools from the ToolNode(s) of an agent/graph"""
    graph_tools: list[dict[str, Any]] = []
    if agent is None:
        return graph_tools

    builder = _get_attr(agent, "builder", None)
    if builder is None:
        return graph_tools

    nodes = _get_attr(builder, "nodes", None)
    if nodes is None or not isinstance(nodes, dict):
        return graph_tools

    for node in nodes.values():
        runnable = _get_attr(node, "runnable", None)
        if runnable is None:
            continue

        if not _is_tool_node(runnable):
            continue

        tools_by_name: dict[str, Any] = _get_attr(runnable, "tools_by_name", {})
        graph_tools.extend(_extract_tools(tools_by_name.values()))

    return graph_tools


def _extract_tools(tools: Iterable[Any]) -> list[dict[str, Any]]:
    """Extract the tool representations from a list of tools"""
    tools_repr = []
    for tool in tools:
        tool_repr = _get_tool_repr_from_langchain_base_tool(tool)
        if tool_repr:
            tools_repr.append(tool_repr)
    return tools_repr


def _get_trigger_ids_from_finished_tasks(
    queued_tasks: Any,
    task_trigger_channels_to_finished_tasks: dict[str, list[Union[str, tuple[str, str]]]],
) -> list[Optional[str]]:
    """
    Return the set of task ids that are responsible for triggering the queued task, returning all the trigger nodes
    that wrote to the channel that the queued task consumes from.

    The one caveat is nodes queued up via `Send` commands. These nodes will have a `__pregel_push` as their trigger, and
    consume from the `__pregel_tasks` channel. We want to pop these instances and associate them one at a time with each
    of the queued tasks.
    """
    task_triggers_from_task = _get_attr(queued_tasks, "triggers", [])
    task_triggers = task_triggers_from_task or []

    trigger_ids: list[Optional[str]] = []

    for trigger in task_triggers:
        if trigger == PREGEL_PUSH:  # handle Pregel Send writes
            pregel_pushes = cast(list[tuple[str, str]], task_trigger_channels_to_finished_tasks.get(PREGEL_TASKS, []))
            pregel_push_index = _find_pregel_push_index(queued_tasks, pregel_pushes)
            if pregel_push_index != -1:
                _, trigger_id = pregel_pushes.pop(pregel_push_index)
                trigger_ids.append(trigger_id)
        else:
            trigger_ids.extend(cast(list[str], task_trigger_channels_to_finished_tasks.get(trigger)) or [])

    return trigger_ids


def _find_pregel_push_index(task: Any, pregel_pushes: list[tuple[str, str]]) -> int:
    """
    Find the index of a specific pregel push node in the list of pregel push nodes
    """
    for i, (pregel_push_node, _) in enumerate(pregel_pushes):
        if pregel_push_node == _get_attr(task, "name", ""):
            return i
    return -1


def _map_channel_writes_to_finished_tasks_ids(
    finished_tasks: dict[str, Any],
) -> dict[str, list[Union[str, tuple[str, str]]]]:
    """
    Maps channel writes for finished tasks to the list of finished tasks ids that wrote to that channel.
    For `__pregel_tasks` writes, we append both the node name for the `Send` object, and the finished task id
    to be used in `_get_trigger_ids_from_finished_tasks`.
    """
    channel_names_to_finished_tasks_ids: dict[str, list[Union[str, tuple[str, str]]]] = {}
    for finished_task_id, finished_task in finished_tasks.items():
        writes: Iterable[tuple[str, Any]] = _get_attr(finished_task, "writes", [])
        for write in writes:
            _append_finished_task_to_channel_writes_map(finished_task_id, write, channel_names_to_finished_tasks_ids)

    return channel_names_to_finished_tasks_ids


def _append_finished_task_to_channel_writes_map(
    finished_task_id: str,
    write: Any,
    channel_names_to_finished_tasks_ids: dict[str, list[Union[str, tuple[str, str]]]],
) -> None:
    """
    Appends the finished task id to the map of channel names to finished tasks ids. If the write represents a
    `__pregel_tasks` write, then append both the node name it's writing to, and the finished task id.
    Otherwise, just append the finished task id to the list of channel writes that the finished task is writing to.
    """
    if not isinstance(write, tuple) or len(write) != 2:
        return
    channel_write_name, channel_write_arg = write
    tasks_for_trigger = channel_names_to_finished_tasks_ids.setdefault(channel_write_name, [])

    if channel_write_name == PREGEL_TASKS:
        pregel_task_node: Optional[str] = _get_attr(channel_write_arg, "node", None)
        if pregel_task_node is None:
            return
        tasks_for_trigger.append((pregel_task_node, finished_task_id))
    else:
        tasks_for_trigger.append(finished_task_id)


def _parse_store_namespace(
    namespace_arg: Any,
) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    if isinstance(namespace_arg, str):
        parts = [p for p in namespace_arg.split("/") if p]
    elif isinstance(namespace_arg, (tuple, list)):
        parts = [str(p) for p in namespace_arg if p is not None and str(p)]
    else:
        return None, None, None, None
    if not parts:
        return None, None, None, None

    scope: Optional[str] = None
    principal: Optional[str] = None
    tenant_id: Optional[str] = None
    safe_parts: list[str] = []
    i = 0
    while i < len(parts):
        seg = parts[i]
        seg_lower = seg.lower()
        seg_scope = _NAMESPACE_SCOPE_MAP.get(seg_lower)
        if seg_scope is not None:
            safe_parts.append(seg_lower)
            if scope is None:
                scope = seg_scope
            if seg_scope in ("user", "session", "agent") and i + 1 < len(parts):
                ident = parts[i + 1]
                if principal is None:
                    principal = ident
                safe_parts.append(hash_identifier(ident))
                i += 2
                continue
            if seg_scope == "org" and i + 1 < len(parts):
                ident = parts[i + 1]
                if tenant_id is None:
                    tenant_id = ident
                safe_parts.append(hash_identifier(ident))
                i += 2
                continue
        else:
            safe_parts.append(hash_identifier(seg))
        i += 1
    return "/".join(safe_parts), scope, principal, tenant_id


def _extract_store_item_record(item: Any, fallback_key: Optional[str] = None) -> dict[str, Any]:
    rec_id = _get_attr(item, "key", None)
    if rec_id is None and fallback_key is not None:
        rec_id = fallback_key
    raw_val = _get_attr(item, "value", None)
    if isinstance(raw_val, str):
        text = raw_val
    elif raw_val is not None:
        text = safe_json(raw_val, ensure_ascii=False) or str(raw_val)
    else:
        text = ""
    record: dict[str, Any] = {"text": text}
    if rec_id is not None and str(rec_id):
        record["id"] = str(rec_id)
    score = _as_float(_get_attr(item, "score", None))
    if score is not None:
        record["score"] = score
    return record


def _default_span_link(span: _LLMObsSpan) -> _SpanLink:
    """
    Create a default input-to-input span link for a given span, if there are no
    referenced spans that represent the causal link. In this case, we assume
    the span is linked to its parent's input.
    """
    parent_id = get_llmobs_parent_id(span)
    return _SpanLink(
        span_id=parent_id or ROOT_PARENT_ID,
        trace_id=format_trace_id(span.trace_id),
        attributes={"from": "input", "to": "input"},
    )
