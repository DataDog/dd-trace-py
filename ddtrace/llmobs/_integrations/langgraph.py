from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace import tracer
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import format_langchain_io
from ddtrace.llmobs._utils import _get_llmobs_parent_id
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.span import Span


node_invokes: Dict[str, Any] = {}


class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"

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
        config = get_argument_value(
            args, kwargs, 1, "config", optional=True
        )  # config might not be present for the root graph node (root node of the trace)

        metadata = config.get("metadata", {}) if isinstance(config, dict) else {}
        instance_id = metadata.get("langgraph_checkpoint_ns", "").split(":")[-1]

        node_invoke = node_invokes[instance_id] = node_invokes.get(instance_id, {})
        span_name = node_invokes.get(instance_id, {}).get("name") or kwargs.get("name", span.name)

        span._set_ctx_items(
            {
                SPAN_KIND: "agent",  # should nodes be workflows? should it be dynamic to if a subgraph is included?
                INPUT_VALUE: format_langchain_io(inputs),
                OUTPUT_VALUE: format_langchain_io(response),
                NAME: span_name,
            }
        )

        node_invoke["span"] = {
            "trace_id": "{:x}".format(span.trace_id),
            "span_id": str(span.span_id),
        }

        span_links = [default_span_link(span)]
        node_invoke_span_links = node_invoke.get("from")
        if node_invoke_span_links is not None and operation == "node":
            span_links = node_invoke_span_links

        if span_links is not None:
            current_span_links = span._get_ctx_item(SPAN_LINKS) or []
            span._set_ctx_item(SPAN_LINKS, current_span_links + span_links)

    def handle_pregel_loop_tick(
        self, finished_tasks: dict, next_tasks: dict, more_tasks: bool, is_subgraph: bool = False
    ):
        """
        Handle a specific tick of the pregel loop.
        Specifically, this function computes incoming and outgoing span links between finished tasks
        and queued tasks in the graph.

        Additionally, it sets the span links at the outer ends of the graph, between the span that invokes
        the graph and the last set of nodes before the graph ends. However, this only happens if the graph
        is not a subgraph, as in those cases the output to output link should happen between tasks, and not
        between the task (subgraph node) and the graph that invoked it.

        We also extract the task name and set it as a possible span name for the node span that is set above
        in the `llmobs_set_tags` method.
        """
        if not self.llmobs_enabled:
            return

        graph_span = (
            tracer.current_span()
        )  # since we're running the the pregel loop, and not in a node, the graph span should be the current span
        graph_caller = _get_nearest_llmobs_ancestor(graph_span) if graph_span else None

        if not more_tasks and graph_span is not None:
            span_links = [
                {**node_invokes[task_id]["span"], "attributes": {"from": "output", "to": "output"}}
                for task_id in finished_tasks.keys()
            ]

            current_span_links = graph_span._get_ctx_item(SPAN_LINKS) or []
            graph_span._set_ctx_item(SPAN_LINKS, current_span_links + span_links)

            if graph_caller is not None and not is_subgraph:
                current_graph_caller_span_links = graph_caller._get_ctx_item(SPAN_LINKS) or []
                graph_caller_span_links = [
                    {
                        "span_id": str(graph_span.span_id) or "undefined",
                        "trace_id": "{:x}".format(graph_caller.trace_id),
                        "attributes": {
                            "from": "output",
                            "to": "output",
                        },
                    }
                ]
                graph_caller._set_ctx_item(SPAN_LINKS, current_graph_caller_span_links + graph_caller_span_links)

            return

        parent_node_names_to_ids = {task.name: task_id for task_id, task in finished_tasks.items()}

        for task_id, task in next_tasks.items():
            task_config = getattr(task, "config", {})
            task_triggers = task_config.get("metadata", {}).get("langgraph_triggers", [])

            parent_node_names = [extract_parent(trigger) for trigger in task_triggers]
            parent_ids: List[str] = [
                parent_node_names_to_ids.get(parent_node_name, "") for parent_node_name in parent_node_names
            ]

            for parent_id in parent_ids:
                parent_span = node_invokes.get(parent_id, {}).get("span")

                node_invoke = node_invokes[task_id] = node_invokes.get(task_id, {})
                node_invoke["name"] = task.name

                if not parent_span:
                    continue

                parent_span_link = {
                    **node_invokes.get(parent_id, {}).get("span", {}),
                    "attributes": {
                        "from": "output",
                        "to": "input",
                    },
                }
                from_nodes = node_invoke["from"] = node_invoke.get("from", [])

                from_nodes.append(parent_span_link)


def extract_parent(trigger: str) -> str:
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


def default_span_link(span: Span):
    """
    Create a default input-to-input span link for a given span, if there are no
    referenced spans that represent the causal link. In this case, we assume
    the span is linked to its parent's input.
    """
    return {
        "span_id": str(_get_llmobs_parent_id(span)) or "undefined",
        "trace_id": "{:x}".format(span.trace_id),
        "attributes": {
            "from": "input",
            "to": "input",
        },
    }
