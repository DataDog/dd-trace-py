from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
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
        **kw: Dict[str, Any],
    ):
        if not self.llmobs_enabled:
            return

        inputs = get_argument_value(args, kwargs, 0, "input")
        span_name = kw.get("name", span.name)
        span_links = []

        if operation == "node":
            config = get_argument_value(args, kwargs, 1, "config")

            metadata = config.get("metadata", {}) if isinstance(config, dict) else {}
            node_instance_id = metadata["langgraph_checkpoint_ns"].split(":")[-1]

            node_invoke = node_invokes[node_instance_id] = node_invokes.get(node_instance_id, {})
            node_invoke["span"] = {
                "trace_id": "{:x}".format(span.trace_id),
                "span_id": str(span.span_id),
            }

            span_links = node_invoke.get("from", [])

        span._set_ctx_items(
            {
                SPAN_KIND: "agent",  # should nodes be workflows? should it be dynamic to if a subgraph is included?
                INPUT_VALUE: inputs,
                OUTPUT_VALUE: response,
                SPAN_LINKS: [
                    span_link for span_link in span_links if ("trace_id" in span_link and "span_id" in span_link)
                ],
                NAME: span_name,
            }
        )

    def handle_pregel_loop_tick(self, finished_tasks: dict, next_tasks: dict):
        if not self.llmobs_enabled:
            return

        parent_node_names_to_ids = {task.name: task_id for task_id, task in finished_tasks.items()}

        for task_id, task in next_tasks.items():
            task_config = getattr(task, "config", {})
            task_triggers = task_config.get("metadata", {}).get("langgraph_triggers", [])

            def extract_parent(trigger):
                split = trigger.split(":")
                if len(split) < 3:
                    return split[0]
                return split[1]

            parent_node_names = [extract_parent(trigger) for trigger in task_triggers]
            parent_ids: List[str] = [
                parent_node_names_to_ids.get(parent_node_name, "") for parent_node_name in parent_node_names
            ]

            for parent_id in parent_ids:
                parent_span_link = node_invokes.get(parent_id, {}).get("span", {})
                if not parent_span_link:
                    continue
                node_invoke = node_invokes[task_id] = node_invokes.get(task_id, {})
                from_nodes = node_invoke["from"] = node_invoke.get("from", [])

                from_nodes.append(parent_span_link)
