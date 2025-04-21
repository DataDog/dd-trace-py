from contextlib import suppress
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

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
from ddtrace.llmobs._integrations.utils import format_langchain_io
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs.utils import LLMObsState
from ddtrace.trace import Span


class LangGraphRoutingContext:
    def __init__(self, state: LLMObsState, current_node_metadata: Dict[str, Any], args: tuple):
        self.state = state
        self.current_node_metadata = current_node_metadata
        self.args = args

    def get_args(self):
        return (self.state, *self.args[1:])

    def __enter__(self):
        self._do_enter()
        return self

    def __aenter__(self):
        self._do_enter()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._do_exit()

    def __aexit__(self, exc_type, exc_value, traceback):
        self._do_exit()

    def _do_enter(self):
        self.state.set_reading(carrier=self.current_node_metadata, carrier_key="influenced_by")

    def _do_exit(self):
        self.state.stop_reading()


class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"
    _graph_nodes_by_task_id: Dict[str, Any] = {}  # maps task_id to dictionary of name, span, and span_links

    def routing_context(self, node_name, args, kwargs) -> Union[LangGraphRoutingContext, suppress]:
        if not self.llmobs_enabled:
            return suppress()

        state = get_argument_value(args, kwargs, 0, "input")
        config = get_argument_value(args, kwargs, 1, "config", optional=True) or {}
        task_id = config.get("metadata", {}).get("langgraph_checkpoint_ns", "").split(":")[-1]
        current_node_metadata = self._graph_nodes_by_task_id.get(task_id, {})
        current_node_name = current_node_metadata.get("name", None)

        if node_name in ("_write", "_route", "_control_branch") or (node_name == current_node_name):
            return suppress()

        return LangGraphRoutingContext(
            state=LLMObsState.from_dict(state), current_node_metadata=current_node_metadata, args=args
        )

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

        span._set_ctx_items(
            {
                SPAN_KIND: "agent" if operation == "graph" else "task",
                INPUT_VALUE: format_langchain_io(inputs.to_state_dict() if isinstance(inputs, LLMObsState) else inputs),
                OUTPUT_VALUE: format_langchain_io(
                    response.to_state_dict() if isinstance(response, LLMObsState) else response
                ),
                NAME: self._graph_nodes_by_task_id.get(instance_id, {}).get("name") or kwargs.get("name", span.name),
                SPAN_LINKS: current_span_links + span_links,
            }
        )

        print(f"\n\nsetting span links for {span._get_ctx_item(NAME)}: {span._get_ctx_item(SPAN_LINKS)}")

        if operation == "graph" and not _is_subgraph(span):
            self._graph_nodes_by_task_id.clear()

    def llmobs_handle_pregel_loop_tick(
        self, finished_tasks: dict, next_tasks: dict, more_tasks: bool, is_subgraph_node: bool = False
    ):
        """Compute incoming and outgoing span links between finished tasks and queued tasks in the graph."""
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
        finished_task_names_to_ids = {task.name: task_id for task_id, task in finished_tasks.items()}
        for task_id, task in next_tasks.items():
            trigger_node_ids = self._link_task_to_parent(task_id, task, finished_task_names_to_ids)
            self._set_llmobs_state(task, task_id, next_tasks, finished_tasks, trigger_node_ids)

    def _handle_finished_graph(self, graph_span: Span, finished_tasks, is_subgraph_node):
        """Create the span links for a finished pregel graph from all finished tasks as the graph span's outputs.
        Generate the output-to-output span links for the last nodes in a pregel graph.
        If the graph isn't a subgraph, add a span link from the graph span to the calling LLMObs parent span.
        Note: is_subgraph_node denotes whether the graph is a subgraph node,
         not whether it is a standalone graph (called internally during a node execution).
        """
        graph_caller_span = _get_nearest_llmobs_ancestor(graph_span) if graph_span else None
        output_span_links = []
        for task_id in finished_tasks.keys():
            graph_node = self._graph_nodes_by_task_id.get(task_id, {})
            graph_node_span = graph_node.get("span")
            graph_node_influenced_by = graph_node.get("influenced_by", {})
            output_span_links.append(
                {
                    **graph_node_span,
                    "attributes": {"from": "output", "to": "output"},
                }
            )
            output_span_links.extend(graph_node_influenced_by)
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

    def _link_task_to_parent(self, task_id, task, finished_task_names_to_ids):
        """Create the span links for a queued task from its triggering parent tasks."""
        task_config = getattr(task, "config", {})
        task_triggers = _normalize_triggers(
            triggers=task_config.get("metadata", {}).get("langgraph_triggers", []),
            finished_tasks=finished_task_names_to_ids,
            next_task=task,
        )

        trigger_node_names = [_extract_parent(trigger) for trigger in task_triggers]
        trigger_node_ids: List[str] = [
            finished_task_names_to_ids.get(trigger_node_name, "") for trigger_node_name in trigger_node_names
        ]

        for node_id in trigger_node_ids:
            queued_node = self._graph_nodes_by_task_id.setdefault(task_id, {})
            queued_node["name"] = getattr(task, "name", "")

            trigger_node: dict = self._graph_nodes_by_task_id.get(node_id, {})
            trigger_node_span = trigger_node.get("span")
            if not trigger_node_span:
                # Subgraphs that are called at the start of the graph need to be named, but don't need any span links
                continue

            span_link = {
                "span_id": trigger_node_span.get("span_id", ""),
                "trace_id": trigger_node_span.get("trace_id", ""),
                "attributes": {"from": "output", "to": "input"},
            }
            span_links = queued_node.setdefault("span_links", [])
            span_links.append(span_link)

            span_links.extend(trigger_node.get("influenced_by", []))

        return trigger_node_ids

    def _set_llmobs_state(self, task, task_id, next_tasks, finished_tasks, trigger_node_ids):
        trigger_nodes = [finished_tasks.get(task_id) for task_id in trigger_node_ids if task_id]
        old_llmobs_states = [getattr(trigger_node, "input", None) for trigger_node in trigger_nodes if trigger_node]
        next_tasks[task_id] = task._replace(
            input=LLMObsState.from_state(llmobs_states=old_llmobs_states, state=task.input, service=LLMObs._instance)
        )

    def get_llmobs_state(self, node_name: str, input_state, output_state: Optional[Dict[str, Any]] = None):
        if (
            not self.llmobs_enabled
            or not isinstance(input_state, LLMObsState)
            or not isinstance(output_state, dict)
            or node_name in ("_write", "_route", "_control_branch")
        ):
            return output_state

        for key in output_state.keys():
            input_state._handle_set(key)

        #  coerce the output state into an LLMObsState
        return LLMObsState.from_state(llmobs_states=[input_state], state=output_state, service=LLMObs._instance)


def _normalize_triggers(triggers, finished_tasks, next_task) -> List[str]:
    """
    Return the default triggers for a LangGraph node.

    For nodes queued up with `langgraph.types.Send`, the triggers are an unhelpful ['__pregel_push'].
    In this case (and in any case with 1 finished task and 1 trigger), we can infer the trigger from
    the one finished task.
    """
    if len(finished_tasks) != 1 or len(triggers) != 1:
        return triggers

    finished_task_name = list(finished_tasks.keys())[0]
    next_task_name = getattr(next_task, "name", "")

    return [f"{finished_task_name}:{next_task_name}"]


def _extract_parent(trigger: str) -> str:
    """
    Extract the parent node name from a trigger string.

    The string could have the format:
    - `parent:child`
    - `parent:routing_logic:child`
    - `branch:parent:routing_logic:child`
    """
    split = trigger.split(":")
    if len(split) < 3:
        return split[0]
    return split[1]


def _default_span_link(span: Span):
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


def _is_subgraph(graph_span):
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
