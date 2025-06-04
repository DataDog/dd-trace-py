from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union
from typing import cast

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


PREGEL_PUSH = "__pregel_push"
PREGEL_TASKS = "__pregel_tasks"


class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"
    _graph_nodes_by_task_id: Dict[str, Any] = {}  # maps task_id to dictionary of name, span, and span_links

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
        config = get_argument_value(args, kwargs, 1, "config", optional=True)
        metadata = _get_attr(config, "metadata", {})
        instance_id = metadata.get("langgraph_checkpoint_ns", "").split(":")[-1]
        invoked_node = self._graph_nodes_by_task_id.setdefault(instance_id, {})
        invoked_node["span"] = {"trace_id": format_trace_id(span.trace_id), "span_id": str(span.span_id)}

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
                NAME: self._graph_nodes_by_task_id.get(instance_id, {}).get("name") or kwargs.get("name", span.name),
                SPAN_LINKS: current_span_links + span_links,
            }
        )

    def llmobs_handle_pregel_loop_tick(
        self, finished_tasks: dict, next_tasks: dict, more_tasks: bool, is_subgraph_node: bool = False
    ):
        """
        Compute incoming and outgoing span links between finished tasks and queued tasks in the graph.

        In the case of finished and queued tasks, any finished tasks that aren't used as links to
        queued tasks should be linked to the encompassing graph span's output.
        """
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

    def _handle_finished_graph(self, graph_span: Span, finished_tasks: dict[str, Any], is_subgraph_node: bool):
        """Create the span links for a finished pregel graph from all finished tasks as the graph span's outputs.
        Generate the output-to-output span links for the last nodes in a pregel graph.
        If the graph isn't a subgraph, add a span link from the graph span to the calling LLMObs parent span.
        Note: is_subgraph_node denotes whether the graph is a subgraph node,
         not whether it is a standalone graph (called internally during a node execution).
        """
        graph_caller_span = _get_nearest_llmobs_ancestor(graph_span) if graph_span else None
        output_span_links = [
            {**self._graph_nodes_by_task_id[task_id]["span"], "attributes": {"from": "output", "to": "output"}}
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
        Handle graph ticks that aren't at the end of the graph execution. Link all next tasks to their parent tasks
        from the dict of finished tasks. Any unused finished tasks (not used as parents for queued tasks) should be
        linked as output --> output links for the outer graph span.
        """
        task_trigger_channels_to_finished_tasks = _map_channel_writes_to_finished_tasks_ids(finished_tasks)

        used_finished_task_ids: Set[str] = set()
        for task_id, task in next_tasks.items():
            queued_node = self._graph_nodes_by_task_id.setdefault(task_id, {})
            queued_node["name"] = getattr(task, "name", "")

            parent_ids = self._link_task_to_parents(task_id, task, task_trigger_channels_to_finished_tasks)
            used_finished_task_ids.update(parent_ids)

        self._link_finished_tasks_without_children(graph_span, finished_tasks, used_finished_task_ids)

    def _link_task_to_parents(
        self,
        task_id: str,
        task,
        task_trigger_channels_to_finished_tasks: Dict[str, List[Union[str, Tuple[str, str]]]],
    ) -> List[str]:
        """
        Create the span links for a queued task from its triggering parent tasks.

        Returns the finished task ids used as parent tasks.
        """
        parent_ids = _get_parent_ids_from_finished_tasks(task, task_trigger_channels_to_finished_tasks)

        for node_id in parent_ids:
            if node_id is None:
                continue

            queued_node: Dict[str, Any] = self._graph_nodes_by_task_id.setdefault(task_id, {})

            trigger_node_span = self._graph_nodes_by_task_id.get(node_id, {}).get("span")
            if not trigger_node_span:
                continue

            span_link = {
                "span_id": trigger_node_span.get("span_id", ""),
                "trace_id": trigger_node_span.get("trace_id", ""),
                "attributes": {"from": "output", "to": "input"},
            }
            span_links: List[Dict[str, Any]] = queued_node.setdefault("span_links", [])
            span_links.append(span_link)

        return parent_ids

    def _link_finished_tasks_without_children(
        self, graph_span: Span, finished_tasks: Dict[str, Any], used_finished_tasks_ids: Set[str]
    ):
        """
        Links any unused finished tasks to the outer graph span with output --> output span links.
        It's possible some of the finished tasks aren't used as parents to queued tasks, but the
        current graph tick is not the last tick.
        """
        unused_finished_task_ids = set(finished_tasks.keys()) - used_finished_tasks_ids
        graph_span_links = graph_span._get_ctx_item(SPAN_LINKS) or []
        for finished_task_id in unused_finished_task_ids:
            node = self._graph_nodes_by_task_id.get(finished_task_id)
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


def _get_parent_ids_from_finished_tasks(
    task,
    task_trigger_channels_to_finished_tasks: Dict[str, List[Union[str, Tuple[str, str]]]],
) -> List[str]:
    """
    Get the set of task ids that are responsible for triggering the queued task (parents).

    Tasks are queued up by means of writes to ephemeral channels. A given finished task will "write"
    to one of these channels, and the pregel look creates new tasks by consuming these writes and translating
    them into triggers for the new tasks.

    Parentage for span linking is computed by looking at the writes from all of the finished tasks grouped by
    the channel written to (this is what `task_trigger_channels_to_finished_tasks` represents). Then, for a given
    task, we can loop over its triggers (which are representative of the channel writes), and extend its parent
    triggers by the finished task ids that wrote to that trigger channel.

    The one caveat is nodes queued up via `Send` commands. These nodes will have a `__pregel_push` as their trigger.
    In this case, finished nodes that queue up these nodes from a `Send` command will write to the `__pregel_tasks`
    channel. For given tasks/nodes whose triggers include `__pregel_push`, we'll find the first instance of a
    `__pregel_tasks` write that is for a node with the current task's name. Since each of these writes can only
    be used for one instance of a node queued by `Send`, we pop it from the list of `__pregel_tasks` writes.
    """
    task_config = getattr(task, "config", {})
    task_triggers_from_task = getattr(task, "triggers", [])
    task_triggers_from_task_config = task_config.get("metadata", {}).get("langgraph_triggers", [])
    task_triggers = task_triggers_from_task or task_triggers_from_task_config or []

    parent_ids: List[str] = []

    for trigger in task_triggers:
        if trigger == PREGEL_PUSH:
            # find the nearest pregel_task that was queued up with this task's (node's) name
            # since Send's are a one-off to be consumed, remove it from the
            # branches to finished_tasks dict entry
            pregel_pushes = cast(List[Tuple[str, str]], task_trigger_channels_to_finished_tasks.get(PREGEL_TASKS, []))
            for _ in range(len(pregel_pushes)):
                pregel_push_node, trigger_id = pregel_pushes.pop(0)
                if pregel_push_node == getattr(task, "name", ""):
                    parent_ids.append(trigger_id)
                    break
                pregel_pushes.append((pregel_push_node, trigger_id))
        else:
            parent_ids.extend((cast(List[str], task_trigger_channels_to_finished_tasks.get(trigger)) or []))

    return parent_ids


def _map_channel_writes_to_finished_tasks_ids(
    finished_tasks: Dict[str, Any]
) -> Dict[str, List[Union[str, Tuple[str, str]]]]:
    """
    Maps channel writes for finished tasks to the list of finished tasks ids that wrote to that channel.
    For `__pregel_tasks` writes, we append both the node name for the `Send` object, and the finished task id
    to be used in `_get_parent_ids_from_finished_tasks`.
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


def _is_subgraph(graph_span: Span) -> bool:
    """Helper to denote whether the LangGraph graph this span represents is a sub-graph or a standalone graph.
    Note that this only considers if this graph is nested in the execution of a larger graph,
    not whether this graph is represented as a single node in the larger graph
    (counterexample being a standalone graph called internally during a node execution).
    """
    graph_caller_span = _get_nearest_llmobs_ancestor(graph_span)
    while graph_caller_span is not None:
        if graph_caller_span.resource.endswith("LangGraph"):
            return True
        graph_caller_span = _get_nearest_llmobs_ancestor(graph_caller_span)
    return False
