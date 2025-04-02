from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import PARENT_ID_KEY
from ddtrace.llmobs._constants import ROOT_PARENT_ID
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.trace import Pin
from ddtrace.trace import Span


log = get_logger(__name__)

CREW_INSTANCE_ID_KEY = "_crew_instance_id"


class CrewAIIntegration(BaseLLMIntegration):
    _integration_name = "crewai"
    # the CrewAI integration's task span linking relies on keeping track of an internal Datadog crew ID,
    # which follows the format "crew_{trace_id}_{root_span_id}".
    _crews_to_task_span_ids: Dict[str, List[str]] = {}  # maps crew ID to list of task span_ids
    _crews_to_tasks: Dict[str, Dict[str, Any]] = {}  # maps crew ID to dictionary of task_id to span_id and span_links
    _planning_crew_ids: List[str] = []  # list of crew IDs that correspond to planning crew instances

    def trace(self, pin: Pin, operation_id: str, submit_to_llmobs: bool = False, **kwargs: Dict[str, Any]) -> Span:
        if kwargs.get("_ddtrace_ctx"):
            tracer_ctx, llmobs_ctx = kwargs["_ddtrace_ctx"]
            pin.tracer.context_provider.activate(tracer_ctx)
            if self.llmobs_enabled and llmobs_ctx:
                core.dispatch("threading.execution", (llmobs_ctx,))

        span = super().trace(pin, operation_id, submit_to_llmobs, **kwargs)

        if kwargs.get("operation") == "crew":
            crew_id = _get_crew_id(span, "crew")
            self._crews_to_task_span_ids[crew_id] = []
            self._crews_to_tasks[crew_id] = {}
            if kwargs.get("planning", False):
                self._planning_crew_ids.append(crew_id)
            span._set_ctx_item(CREW_INSTANCE_ID_KEY, crew_id)
            return span
        if kwargs.get("operation") == "task":
            crew_id = _get_crew_id(span, "task")
            task_id = kwargs.get("instance_id", "")
            self._crews_to_task_span_ids.get(crew_id, []).append(str(span.span_id))
            task_node = self._crews_to_tasks.get(crew_id, {}).setdefault(str(task_id), {})
            task_node["span_id"] = str(span.span_id)
        return span

    def _get_current_ctx(self, pin):
        """Extract current tracer and llmobs contexts to propagate across threads during async task execution."""
        curr_trace_ctx = pin.tracer.current_trace_context()
        if self.llmobs_enabled:
            curr_llmobs_ctx = core.dispatch_with_results("threading.submit", ()).llmobs_ctx.value
            return curr_trace_ctx, curr_llmobs_ctx
        return curr_trace_ctx, None

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span._set_ctx_item(SPAN_KIND, "workflow" if operation == "crew" else operation)
        if operation == "crew":
            crew_id = _get_crew_id(span, "crew")
            self._llmobs_set_tags_crew(span, args, kwargs, response)
            self._crews_to_task_span_ids.pop(crew_id, None)
            self._crews_to_tasks.pop(crew_id, None)
            if crew_id in self._planning_crew_ids:
                self._planning_crew_ids.remove(crew_id)
        elif operation == "task":
            self._llmobs_set_tags_task(span, args, kwargs, response)
        elif operation == "agent":
            self._llmobs_set_tags_agent(span, args, kwargs, response)
        elif operation == "tool":
            self._llmobs_set_tags_tool(span, args, kwargs, response)

    def _llmobs_set_tags_crew(self, span, args, kwargs, response):
        crew_id = _get_crew_id(span, "crew")
        task_span_ids = self._crews_to_task_span_ids.get(crew_id, [])
        if self.span_linking_enabled and task_span_ids:
            last_task_span_id = task_span_ids[-1]
            span_link = {
                "span_id": last_task_span_id,
                "trace_id": format_trace_id(span.trace_id),
                "attributes": {"from": "output", "to": "output"},
            }
            curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
            span._set_ctx_item(SPAN_LINKS, curr_span_links + [span_link])
        # TODO: Add metadata if relevant?
        inputs = get_argument_value(args, kwargs, 0, "inputs", optional=True) or ""
        span._set_ctx_items({INPUT_VALUE: inputs, NAME: "CrewAI Crew"})
        if span.error:
            return
        span._set_ctx_item(OUTPUT_VALUE, getattr(response, "raw", ""))

    def _llmobs_set_tags_task(self, span, args, kwargs, response):
        crew_id = _get_crew_id(span, "task")
        task_instance = kwargs.get("instance")
        task_id = getattr(task_instance, "id", None)
        task_name = getattr(task_instance, "name", "")
        task_description = getattr(task_instance, "description", "")
        metadata = {
            "expected_output": getattr(task_instance, "expected_output", ""),
            "context": args[1] if args and len(args) >= 2 else "",
            "async_execution": getattr(task_instance, "async_execution", False),
            "human_input": getattr(task_instance, "human_input", False),
            "output_file": getattr(task_instance, "output_file", ""),
        }
        if self.span_linking_enabled and task_id:
            span_links = self._crews_to_tasks[crew_id].get(str(task_id), {}).get("span_links", [])
            if self._is_planning_task(span):
                parent_span = _get_nearest_llmobs_ancestor(span)
                span_link = {
                    "span_id": str(parent_span.span_id),
                    "trace_id": format_trace_id(span.trace_id),
                    "attributes": {"from": "input", "to": "input"},
                }
                span_links.append(span_link)
            curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
            span._set_ctx_item(SPAN_LINKS, curr_span_links + span_links)
        span._set_ctx_items(
            {NAME: task_name if task_name else "CrewAI Task", METADATA: metadata, INPUT_VALUE: task_description}
        )
        if span.error:
            return
        span._set_ctx_item(OUTPUT_VALUE, getattr(response, "raw", ""))

    def _llmobs_set_tags_agent(self, span, args, kwargs, response):
        """Set span links and metadata for agent spans.
        Agent spans are 1:1 with its parent (task/tool) span, so we link them directly here, even on the parent itself.
        """
        agent_instance = kwargs.get("instance")
        agent_role = getattr(agent_instance, "role", "")
        agent_goal = getattr(agent_instance, "goal", "")
        agent_backstory = getattr(agent_instance, "backstory", "")
        task_description = getattr(kwargs.get("task"), "description", "")
        context = get_argument_value(args, kwargs, 1, "context", optional=True) or ""
        if self.span_linking_enabled:
            parent_span = _get_nearest_llmobs_ancestor(span)
            parent_span_link = {
                "span_id": str(span.span_id),
                "trace_id": format_trace_id(span.trace_id),
                "attributes": {"from": "output", "to": "output"},
            }
            curr_span_links = parent_span._get_ctx_item(SPAN_LINKS) or []
            parent_span._set_ctx_item(SPAN_LINKS, curr_span_links + [parent_span_link])
            span_link = {
                "span_id": str(parent_span.span_id),
                "trace_id": format_trace_id(span.trace_id),
                "attributes": {"from": "input", "to": "input"},
            }
            curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
            span._set_ctx_item(SPAN_LINKS, curr_span_links + [span_link])
        span._set_ctx_items(
            {
                NAME: agent_role if agent_role else "CrewAI Agent",
                METADATA: {"description": agent_goal, "backstory": agent_backstory},
                INPUT_VALUE: {"context": context, "input": task_description},
            }
        )
        if span.error:
            return
        span._set_ctx_item(OUTPUT_VALUE, response)

    def _llmobs_set_tags_tool(self, span, args, kwargs, response):
        tool_instance = kwargs.get("instance")
        tool_name = getattr(tool_instance, "name", "")
        description = _extract_tool_description_field(getattr(tool_instance, "description", ""))
        span._set_ctx_items(
            {
                NAME: tool_name if tool_name else "CrewAI Tool",
                METADATA: {"description": description},
                INPUT_VALUE: kwargs.get("input", ""),
            }
        )
        if span.error:
            return
        span._set_ctx_item(OUTPUT_VALUE, response)

    def _llmobs_set_span_link_on_task(self, span, args, kwargs):
        """Set span links for the next queued task in a CrewAI workflow.
        This happens between task executions, (the current span is the crew span and the task span hasn't started yet)
        so we create span links to be set on the task span once it starts later.
        We rely on 3 cases to determine the appropriate span links:
        1. queued_task.context is set with the most recently finished tasks that directly feed into the queued task.
        2. queued_task.context is empty and there are no finished task outputs,
            meaning this is the first task (tasks if async) in the crew workflow.
        3. queued_task.context is empty, but there are n finished task outputs,
            meaning that the last n task outputs should be the pre-requisite tasks for the queued task.
        """
        if not self.llmobs_enabled or not self.span_linking_enabled:
            return
        queued_task = get_argument_value(args, kwargs, 0, "task", optional=True)
        finished_task_outputs = get_argument_value(args, kwargs, 1, "task_outputs", optional=True)
        if queued_task is None or finished_task_outputs is None:
            log.debug("No queued task or finished task outputs found, skipping span linking.")
            return
        if span is None:
            log.debug("No current span found, skipping span linking.")
            return
        queued_task_id = getattr(queued_task, "id", "")
        crew_id = _get_crew_id(span, "crew")
        is_planning_crew_instance = crew_id in self._planning_crew_ids
        queued_task_node = self._crews_to_tasks.get(crew_id, {}).setdefault(str(queued_task_id), {})
        span_links = []

        if queued_task.context:
            for finished_task in queued_task.context:
                finished_task_id = getattr(finished_task, "id", "")
                finished_task_node = self._crews_to_tasks.get(crew_id, {}).get(str(finished_task_id), {})
                finished_task_span_id = finished_task_node.get("span_id")
                span_links.append(
                    {
                        "span_id": finished_task_span_id,
                        "trace_id": format_trace_id(span.trace_id),
                        "attributes": {"from": "output", "to": "input"},
                    }
                )
            queued_task_node["span_links"] = span_links
            return
        if not finished_task_outputs and not is_planning_crew_instance:
            queued_task_node["span_links"] = [
                {
                    "span_id": str(span.span_id) if span else ROOT_PARENT_ID,
                    "trace_id": format_trace_id(span.trace_id),
                    "attributes": {"from": "input", "to": "input"},
                }
            ]
            return
        if is_planning_crew_instance and self._crews_to_task_span_ids.get(crew_id, []):
            planning_task_span_id = self._crews_to_task_span_ids[crew_id][-1]
            queued_task_node["span_links"] = [
                {
                    "span_id": planning_task_span_id if span else ROOT_PARENT_ID,
                    "trace_id": format_trace_id(span.trace_id),
                    "attributes": {"from": "output", "to": "input"},
                }
            ]
            return

        finished_task_spans = self._crews_to_task_span_ids.get(crew_id, [])
        num_tasks_to_link = min(len(finished_task_outputs), len(finished_task_spans))
        for i in range(1, num_tasks_to_link + 1):  # Iterate backwards through last n finished tasks
            finished_task_span_id = finished_task_spans[-i]
            span_links.append(
                {
                    "span_id": finished_task_span_id,
                    "trace_id": format_trace_id(span.trace_id),
                    "attributes": {"from": "output", "to": "input"},
                }
            )
        queued_task_node["span_links"] = span_links
        return

    def _is_planning_task(self, span):
        """Check if the current task is a planning task, since we need to add span links manually for planning tasks.
        This is done by checking if the task span is the first task in the crew execution and
        planning is enabled on the crew instance.
        """
        crew_id = _get_crew_id(span, "task")
        if not self._crews_to_task_span_ids.get(crew_id) or self._crews_to_task_span_ids[crew_id][0] != str(
            span.span_id
        ):
            return False
        return crew_id in self._planning_crew_ids


def _extract_tool_description_field(tool_description: str):
    fields = tool_description.rsplit("Tool Description: ", 1)
    if len(fields) == 1:
        return tool_description
    return fields[-1]


def _get_crew_id(span, operation):
    """Return the crew ID from a crew or task span."""
    if operation == "crew":
        return f"crew_{span.trace_id}_{span.span_id}"
    if operation == "task":
        parent_id = span._get_ctx_item(PARENT_ID_KEY) or span.parent_id
        return f"crew_{span.trace_id}_{parent_id}"
    return f"{span.trace_id}"
