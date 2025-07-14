from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union
from typing import cast
from weakref import WeakKeyDictionary

from ddtrace._trace.pin import Pin
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.constants import LANGGRAPH_ASTREAM_OUTPUT
from ddtrace.llmobs._integrations.utils import format_langchain_io
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.trace import Span


logger = get_logger(__name__)


PREGEL_PUSH = "__pregel_push"  # represents a task queued up by a `Send` command
PREGEL_TASKS = "__pregel_tasks"  # name of ephemeral channel that pregel `Send` commands write to

EXCLUDED_MODEL_SETTINGS_KEYS = [
    "model",
    "model_name",
    "_type",
]


class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"
    _graph_nodes_for_graph_by_task_id: WeakKeyDictionary[Span, Dict[str, Any]] = WeakKeyDictionary()
    _react_agents_manifests: WeakKeyDictionary[Any, Dict[str, Any]] = WeakKeyDictionary()
    _graph_spans_to_graph_instances: WeakKeyDictionary[Span, Any] = WeakKeyDictionary()

    def trace(
        self,
        pin: Pin,
        operation_id: str,
        submit_to_llmobs: bool = False,
        is_graph: bool = False,
        instance=None,
        **kwargs,
    ) -> Span:
        span = super().trace(pin, operation_id, submit_to_llmobs, **kwargs)

        if instance and is_graph:
            self._graph_spans_to_graph_instances[span] = instance

        return span

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",  # oneof graph, node
    ):
        if not self.llmobs_enabled:
            return

        inputs = get_argument_value(args, kwargs, 0, "input")
        config = get_argument_value(args, kwargs, 1, "config", optional=True) or {}
        metadata = _get_attr(config, "metadata", {})
        instance_id = metadata.get("langgraph_checkpoint_ns", "").split(":")[-1]
        is_subgraph = config.get("metadata", {}).get("_dd.subgraph", False)

        invoked_node = (
            self._get_node_metadata_from_span(span, instance_id) if operation == "node" or is_subgraph else {}
        )

        span_links = [_default_span_link(span)]
        invoked_node_span_links = invoked_node.get("span_links")
        if invoked_node_span_links is not None:
            span_links = invoked_node_span_links
        current_span_links = span._get_ctx_item(SPAN_LINKS) or []

        def maybe_format_langchain_io(messages):
            if messages is None:
                return None
            return format_langchain_io(messages)

        span._set_ctx_items(
            {
                SPAN_KIND: "agent" if operation == "graph" else "task",
                INPUT_VALUE: format_langchain_io(inputs),
                OUTPUT_VALUE: maybe_format_langchain_io(response)
                or maybe_format_langchain_io(span._get_ctx_item(LANGGRAPH_ASTREAM_OUTPUT)),
                NAME: invoked_node.get("name") or kwargs.get("name", span.name),
                SPAN_LINKS: current_span_links + span_links,
            }
        )

        if operation == "graph":
            agent = kwargs.get("instance")
            agent_manifest = self._get_agent_manifest(span, agent)
            metadata = span._get_ctx_item(METADATA) or {}
            metadata["agent_manifest"] = agent_manifest
            span._set_ctx_item(METADATA, metadata)

    def _get_agent_manifest(self, span: Span, agent) -> Optional[Dict[str, Any]]:
        """
        Gets the agent manifest for a given agent.

        If the agent is a react agent, we have already stored the manifest in the _react_agents weak key dictionary.
        Otherwise, we try and do some inference to get the manifest.
        """
        if agent is None:
            return None

        agent_manifest = self._react_agents_manifests.get(agent)
        if agent_manifest is None:
            tools = _get_tools_from_tool_nodes(agent)
            agent_manifest = {"name": agent.name or "LangGraph", "tools": tools}
            self._react_agents_manifests[agent] = agent_manifest

        parent_span = _get_nearest_llmobs_ancestor(span)
        if parent_span is None:
            return agent_manifest

        parent_graph = self._graph_spans_to_graph_instances.get(parent_span)
        if parent_graph is None:
            return agent_manifest

        handoffs = _get_agent_handoffs(agent, parent_graph)
        agent_manifest["handoffs"] = handoffs

        return agent_manifest

    def _get_node_metadata_from_span(self, span: Span, instance_id: str) -> Dict[str, Any]:
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

    def llmobs_handle_agent_manifest(self, agent, args: tuple, kwargs: dict):
        if not self.llmobs_enabled:
            return

        model = get_argument_value(args, kwargs, 0, "model")
        model_name, model_provider, model_settings = _get_model_info(model)

        agent_tools: List[Any] = get_argument_value(args, kwargs, 1, "tools") or []
        system_prompt: Optional[str] = kwargs.get("prompt")
        name: Optional[str] = kwargs.get("name")

        tools = [{"name": tool.name, "description": tool.description} for tool in agent_tools]

        agent_manifest = {
            "model": model_name,
            "model_provider": model_provider,
            "model_settings": model_settings,
            "tools": tools,
        }

        if system_prompt:
            agent_manifest["instructions"] = system_prompt

        if name:
            agent_manifest["name"] = name

        self._react_agents_manifests[agent] = agent_manifest

    def llmobs_handle_pregel_loop_tick(
        self, finished_tasks: dict, next_tasks: dict, more_tasks: bool, is_subgraph_node: bool = False
    ):
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

    def _handle_finished_graph(self, graph_span: Span, finished_tasks: dict[str, Any], is_subgraph_node: bool):
        """Create the span links for a finished pregel graph from all finished tasks as the graph span's outputs.
        Generate the output-to-output span links for the last nodes in a pregel graph.
        If the graph isn't a subgraph, add a span link from the graph span to the calling LLMObs parent span.
        Note: is_subgraph_node denotes whether the graph is a subgraph node,
         not whether it is a standalone graph (called internally during a node execution).
        """
        graph_caller_span = _get_nearest_llmobs_ancestor(graph_span) if graph_span else None
        output_span_links = [
            {
                **self._graph_nodes_for_graph_by_task_id[graph_span][task_id]["span"],
                "attributes": {"from": "output", "to": "output"},
            }
            for task_id in finished_tasks.keys()
        ]
        graph_span_span_links = graph_span._get_ctx_item(SPAN_LINKS) or []
        graph_span._set_ctx_item(SPAN_LINKS, graph_span_span_links + output_span_links)
        if graph_caller_span is not None and not is_subgraph_node:
            graph_caller_span_links = graph_caller_span._get_ctx_item(SPAN_LINKS) or []
            span_links = [
                {
                    "span_id": str(graph_span.span_id) or "undefined",
                    "trace_id": format_trace_id(graph_span.trace_id),
                    "attributes": {"from": "output", "to": "output"},
                }
            ]
            graph_caller_span._set_ctx_item(SPAN_LINKS, graph_caller_span_links + span_links)

        return

    def _handle_intermediary_graph_tick(self, graph_span: Span, next_tasks: dict, finished_tasks: dict):
        """
        Handle graph ticks that aren't at the end of the graph execution. Link all next tasks to their trigger tasks
        from the dict of finished tasks. Any finished tasks that do not trigger any queued tasks should be
        linked as output --> output links for the outer graph span.
        """
        task_trigger_channels_to_finished_tasks = _map_channel_writes_to_finished_tasks_ids(finished_tasks)

        used_finished_task_ids: Set[str] = set()
        for task_id, task in next_tasks.items():
            queued_node = self._graph_nodes_for_graph_by_task_id.setdefault(graph_span, {}).setdefault(task_id, {})
            queued_node["name"] = getattr(task, "name", "")

            trigger_ids = self._link_task_to_triggers(
                task, queued_node, graph_span, task_trigger_channels_to_finished_tasks
            )
            used_finished_task_ids.update(trigger_ids)

        self._link_standalone_terminal_tasks(graph_span, finished_tasks, used_finished_task_ids)

    def _link_task_to_triggers(
        self,
        task,
        queued_node,
        graph_span: Span,
        task_trigger_channels_to_finished_tasks: Dict[str, List[Union[str, Tuple[str, str]]]],
    ) -> List[str]:
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

            span_link = {
                "span_id": trigger_node_span.get("span_id", ""),
                "trace_id": trigger_node_span.get("trace_id", ""),
                "attributes": {"from": "output", "to": "input"},
            }
            span_links: List[Dict[str, Any]] = queued_node.setdefault("span_links", [])
            span_links.append(span_link)

        return trigger_ids

    def _link_standalone_terminal_tasks(
        self, graph_span: Span, finished_tasks: Dict[str, Any], used_finished_tasks_ids: Set[str]
    ):
        """
        Default handler that links any finished tasks not used as triggers for queued tasks to the outer graph span.
        """
        standalone_terminal_task_ids = set(finished_tasks.keys()) - used_finished_tasks_ids
        graph_span_links = graph_span._get_ctx_item(SPAN_LINKS) or []
        for finished_task_id in standalone_terminal_task_ids:
            node = self._graph_nodes_for_graph_by_task_id.get(graph_span, {}).get(finished_task_id)
            if node is None:
                continue

            span = node.get("span")
            if span is None:
                continue

            graph_span_links.append(
                {
                    "span_id": span.get("span_id", ""),
                    "trace_id": span.get("trace_id", ""),
                    "attributes": {"from": "output", "to": "output"},
                }
            )
        graph_span._set_ctx_item(SPAN_LINKS, graph_span_links)


def _get_agent_handoffs(agent, agent_parent) -> List[Union[str, Dict[str, Any]]]:
    handoffs: List[Union[str, Dict[str, Any]]] = []

    disallowed_handoff_nodes = [agent.name, "__start__", "__end__"]

    if agent_parent is None or agent is None:
        return handoffs

    builder = getattr(agent_parent, "builder", None)
    if builder is None:
        return handoffs

    agent_name = getattr(agent, "name", None)
    if agent_name is None:
        return handoffs

    edges_handoffs = _get_handoffs_from_edges(builder, agent_name, disallowed_handoff_nodes)
    handoffs.extend(edges_handoffs)

    branches_handoffs = _get_handoffs_from_branches(builder, agent_name, disallowed_handoff_nodes, agent_parent)
    handoffs.extend(branches_handoffs)

    return handoffs


def _get_handoffs_from_edges(
    builder, agent_name: str, disallowed_handoff_nodes: List[str]
) -> List[Union[str, Dict[str, Any]]]:
    handoffs: List[Union[str, Dict[str, Any]]] = []

    edges: Optional[Set[Tuple[str, str]]] = getattr(builder, "edges", None)
    if edges is None:
        return handoffs

    for from_node, to_node in edges:
        if from_node == agent_name and to_node not in disallowed_handoff_nodes:
            handoffs.append(to_node)
    return handoffs


def _get_handoffs_from_branches(
    builder,
    agent_name: str,
    disallowed_handoff_nodes: List[str],
    agent_parent,
) -> List[Union[str, Dict[str, Any]]]:
    handoffs: List[Union[str, Dict[str, Any]]] = []

    branches: Optional[Dict[str, Dict[str, Any]]] = getattr(builder, "branches", None)
    if branches is None:
        return handoffs

    conditional_edges = dict(branches)
    conditional_edge = conditional_edges.get(agent_name, {})

    parent_nodes_dict = getattr(agent_parent, "nodes", {}) or {}
    parent_nodes = parent_nodes_dict.keys()

    if not conditional_edge:
        return handoffs

    for routing_func, branch in conditional_edge.items():
        node_ends_from_handoff_dict = getattr(branch, "ends", {}) or {}
        node_ends_from_handoff = node_ends_from_handoff_dict.values()

        possible_handoff_nodes = node_ends_from_handoff or parent_nodes
        if not possible_handoff_nodes:
            continue

        possible_handoff_nodes = [node for node in possible_handoff_nodes if node not in disallowed_handoff_nodes]
        if not possible_handoff_nodes:
            handoffs.append({"tool_name": routing_func})
        else:
            for node in possible_handoff_nodes:
                handoffs.append({"tool_name": routing_func, "agent_name": node})

    return handoffs


def _get_model_info(model) -> Tuple[Optional[str], Optional[str], Dict[str, Any]]:
    """Get the model name, provider, and settings from a langchain llm"""
    model_name = getattr(model, "model_name", None)
    model_provider = _get_model_provider(model)
    model_settings = _get_model_settings(model)
    return model_name, model_provider, model_settings


def _get_model_provider(model) -> Optional[str]:
    """Get the model provider from a langchain llm"""
    model_provider_info_fn = getattr(model, "_get_ls_params", None)
    if model_provider_info_fn is None or not callable(model_provider_info_fn):
        return None

    model_provider_info: Dict[str, Any] = model_provider_info_fn()
    return model_provider_info.get("ls_provider", None)


def _get_model_settings(model) -> Dict[str, Any]:
    """Get the model settings from a langchain llm"""
    invocation_params_fn = getattr(model, "_get_invocation_params", None)
    if invocation_params_fn is None or not callable(invocation_params_fn):
        return {}

    invocation_params: dict = invocation_params_fn()
    return {key: value for key, value in invocation_params.items() if key not in EXCLUDED_MODEL_SETTINGS_KEYS and value}


def _get_tools_from_tool_nodes(agent) -> List[str]:
    """Get the tools from the ToolNode(s) of an agent/graph"""
    tool_names: Set[str] = set()
    if agent is None:
        return list(tool_names)

    builder = getattr(agent, "builder", None)
    if builder is None:
        return list(tool_names)

    nodes: Optional[Dict[str, Any]] = getattr(builder, "nodes", None)
    if nodes is None:
        return list(tool_names)

    for node in nodes.values():
        runnable = getattr(node, "runnable", None)
        if runnable is None:
            continue

        tools_by_name: Optional[Dict[str, Any]] = getattr(runnable, "tools_by_name", None)
        if tools_by_name is None:
            continue

        tool_names.update(tools_by_name.keys())

    return list(tool_names)


def _get_trigger_ids_from_finished_tasks(
    queued_tasks,
    task_trigger_channels_to_finished_tasks: Dict[str, List[Union[str, Tuple[str, str]]]],
) -> List[str]:
    """
    Return the set of task ids that are responsible for triggering the queued task, returning all the trigger nodes
    that wrote to the channel that the queued task consumes from.

    The one caveat is nodes queued up via `Send` commands. These nodes will have a `__pregel_push` as their trigger, and
    consume from the `__pregel_tasks` channel. We want to pop these instances and associate them one at a time with each
    of the queued tasks.
    """
    task_triggers_from_task = getattr(queued_tasks, "triggers", [])
    task_triggers = task_triggers_from_task or []

    trigger_ids: List[str] = []

    for trigger in task_triggers:
        if trigger == PREGEL_PUSH:  # handle Pregel Send writes
            pregel_pushes = cast(List[Tuple[str, str]], task_trigger_channels_to_finished_tasks.get(PREGEL_TASKS, []))
            pregel_push_index = _find_pregel_push_index(queued_tasks, pregel_pushes)
            if pregel_push_index != -1:
                _, trigger_id = pregel_pushes.pop(pregel_push_index)
                trigger_ids.append(trigger_id)
        else:
            trigger_ids.extend((cast(List[str], task_trigger_channels_to_finished_tasks.get(trigger)) or []))

    return trigger_ids


def _find_pregel_push_index(task, pregel_pushes: List[Tuple[str, str]]) -> int:
    """
    Find the index of a specific pregel push node in the list of pregel push nodes
    """
    for i, (pregel_push_node, _) in enumerate(pregel_pushes):
        if pregel_push_node == getattr(task, "name", ""):
            return i
    return -1


def _map_channel_writes_to_finished_tasks_ids(
    finished_tasks: Dict[str, Any]
) -> Dict[str, List[Union[str, Tuple[str, str]]]]:
    """
    Maps channel writes for finished tasks to the list of finished tasks ids that wrote to that channel.
    For `__pregel_tasks` writes, we append both the node name for the `Send` object, and the finished task id
    to be used in `_get_trigger_ids_from_finished_tasks`.
    """
    channel_names_to_finished_tasks_ids: Dict[str, List[Union[str, Tuple[str, str]]]] = {}
    for finished_task_id, finished_task in finished_tasks.items():
        writes: Iterable[Tuple[str, Any]] = getattr(finished_task, "writes", [])
        for write in writes:
            _append_finished_task_to_channel_writes_map(finished_task_id, write, channel_names_to_finished_tasks_ids)

    return channel_names_to_finished_tasks_ids


def _append_finished_task_to_channel_writes_map(
    finished_task_id: str, write, channel_names_to_finished_tasks_ids: Dict[str, List[Union[str, Tuple[str, str]]]]
):
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
        pregel_task_node: Optional[str] = getattr(channel_write_arg, "node", None)
        if pregel_task_node is None:
            return
        tasks_for_trigger.append((pregel_task_node, finished_task_id))
    else:
        tasks_for_trigger.append(finished_task_id)


def _default_span_link(span: Span) -> dict:
    """
    Create a default input-to-input span link for a given span, if there are no
    referenced spans that represent the causal link. In this case, we assume
    the span is linked to its parent's input.
    """
    return {
        "span_id": span._get_ctx_item(PARENT_ID_KEY) or ROOT_PARENT_ID,
        "trace_id": format_trace_id(span.trace_id),
        "attributes": {"from": "input", "to": "input"},
    }
