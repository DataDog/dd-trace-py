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

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import INPUT_VALUE
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


class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"
    _graph_nodes_for_graph_by_task_id: WeakKeyDictionary[Span, Dict[str, Any]] = WeakKeyDictionary()

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
