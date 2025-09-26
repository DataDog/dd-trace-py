from collections.abc import Iterable
import json
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from weakref import WeakKeyDictionary

from ddtrace._trace.pin import Pin
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._constants import AGENT_MANIFEST
from ddtrace.llmobs._constants import DISPATCH_ON_TOOL_CALL
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
from ddtrace.llmobs._utils import safe_json
from ddtrace.llmobs.types import _SpanLink
from ddtrace.trace import Span


log = get_logger(__name__)


OP_NAMES_TO_SPAN_KIND = {
    "crew": "workflow",
    "task": "task",
    "agent": "agent",
    "tool": "tool",
    "flow": "workflow",
    "flow_method": "task",
}


class CrewAIIntegration(BaseLLMIntegration):
    _integration_name = "crewai"
    # the CrewAI integration's task span linking relies on keeping track of an internal Datadog crew ID,
    # which follows the format "crew_{trace_id}_{root_span_id}".
    _crews_to_task_span_ids: Dict[str, List[str]] = {}  # maps crew ID to list of task span_ids
    _crews_to_tasks: Dict[str, Dict[str, Any]] = {}  # maps crew ID to dictionary of task_id to span_id and span_links
    _planning_crew_ids: List[str] = []  # list of crew IDs that correspond to planning crew instances
    _flow_span_to_method_to_span_dict: WeakKeyDictionary[Span, Dict[str, Dict[str, Any]]] = WeakKeyDictionary()

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
            return span
        if kwargs.get("operation") == "task":
            crew_id = _get_crew_id(span, "task")
            task_id = kwargs.get("instance_id", "")
            self._crews_to_task_span_ids.get(crew_id, []).append(str(span.span_id))
            task_node = self._crews_to_tasks.get(crew_id, {}).setdefault(str(task_id), {})
            task_node["span_id"] = str(span.span_id)
        if kwargs.get("operation") == "flow":
            self._flow_span_to_method_to_span_dict[span] = {}
        if kwargs.get("operation") == "flow_method":
            span_name = kwargs.get("span_name", "")
            method_name: str = span_name if isinstance(span_name, str) else ""
            if span._parent is None:
                return span
            span_dict = self._flow_span_to_method_to_span_dict.get(span._parent, {}).setdefault(method_name, {})
            span_dict.update({"span_id": str(span.span_id)})
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
        span._set_ctx_item(SPAN_KIND, OP_NAMES_TO_SPAN_KIND.get(operation, "task"))
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
        elif operation == "flow":
            self._llmobs_set_tags_flow(span, args, kwargs, response)
        elif operation == "flow_method":
            self._llmobs_set_tags_flow_method(span, args, kwargs, response)

    def _llmobs_set_tags_crew(self, span, args, kwargs, response):
        crew_instance = kwargs.pop("_dd.instance", None)
        crew_id = _get_crew_id(span, "crew")
        task_span_ids = self._crews_to_task_span_ids.get(crew_id, [])
        if task_span_ids:
            last_task_span_id = task_span_ids[-1]
            span_link = _SpanLink(
                span_id=last_task_span_id,
                trace_id=format_trace_id(span.trace_id),
                attributes={"from": "output", "to": "output"},
            )
            curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
            span._set_ctx_item(SPAN_LINKS, curr_span_links + [span_link])
        metadata = {
            "process": getattr(crew_instance, "process", ""),
            "planning": getattr(crew_instance, "planning", ""),
            "cache": getattr(crew_instance, "cache", ""),
            "verbose": getattr(crew_instance, "verbose", ""),
            "memory": getattr(crew_instance, "memory", ""),
        }
        inputs = get_argument_value(args, kwargs, 0, "inputs", optional=True) or ""
        span._set_ctx_items({INPUT_VALUE: inputs, NAME: "CrewAI Crew", METADATA: metadata})
        if span.error:
            return
        span._set_ctx_item(OUTPUT_VALUE, getattr(response, "raw", ""))

    def _llmobs_set_tags_task(self, span, args, kwargs, response):
        crew_id = _get_crew_id(span, "task")
        task_instance = kwargs.pop("_dd.instance", None)
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
        if task_id:
            span_links = self._crews_to_tasks[crew_id].get(str(task_id), {}).get("span_links", [])
            if self._is_planning_task(span):
                parent_span = _get_nearest_llmobs_ancestor(span)
                span_link = _SpanLink(
                    span_id=str(parent_span.span_id),
                    trace_id=format_trace_id(span.trace_id),
                    attributes={"from": "input", "to": "input"},
                )
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
        agent_instance = kwargs.get("_dd.instance", None)
        self._tag_agent_manifest(span, agent_instance)
        agent_role = getattr(agent_instance, "role", "")
        task_description = getattr(kwargs.get("task"), "description", "")
        context = get_argument_value(args, kwargs, 1, "context", optional=True) or ""

        parent_span = _get_nearest_llmobs_ancestor(span)
        parent_span_link = _SpanLink(
            span_id=str(span.span_id),
            trace_id=format_trace_id(span.trace_id),
            attributes={"from": "output", "to": "output"},
        )
        curr_span_links = parent_span._get_ctx_item(SPAN_LINKS) or []
        parent_span._set_ctx_item(SPAN_LINKS, curr_span_links + [parent_span_link])
        span_link = _SpanLink(
            span_id=str(parent_span.span_id),
            trace_id=format_trace_id(span.trace_id),
            attributes={"from": "input", "to": "input"},
        )
        curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
        span._set_ctx_items(
            {
                NAME: agent_role if agent_role else "CrewAI Agent",
                INPUT_VALUE: {"context": context, "input": task_description},
                SPAN_LINKS: curr_span_links + [span_link],
            }
        )
        if span.error:
            return
        span._set_ctx_item(OUTPUT_VALUE, response)

    def _llmobs_set_tags_tool(self, span, args, kwargs, response):
        tool_instance = kwargs.pop("_dd.instance", None)
        tool_name = getattr(tool_instance, "name", "")
        description = _extract_tool_description_field(getattr(tool_instance, "description", ""))
        tool_input = kwargs.get("input", "")
        span._set_ctx_items(
            {
                NAME: tool_name if tool_name else "CrewAI Tool",
                METADATA: {"description": description},
                INPUT_VALUE: tool_input,
            }
        )
        if span.error:
            return
        span._set_ctx_item(OUTPUT_VALUE, response)

        try:
            if isinstance(tool_input, str):
                tool_input = json.loads(tool_input)
            tool_input = {k: v for k, v in tool_input.items() if k != "security_context"}
        except (json.JSONDecodeError, TypeError):
            log.warning("Failed to filter out security context from tool input.", exc_info=True)

        core.dispatch(
            DISPATCH_ON_TOOL_CALL,
            (tool_name, safe_json(tool_input), "function", span),
        )

    def _tag_agent_manifest(self, span, agent):
        if not agent:
            return

        manifest = {}
        manifest["framework"] = "CrewAI"
        manifest["name"] = agent.role if hasattr(agent, "role") and agent.role else "CrewAI Agent"
        if hasattr(agent, "goal"):
            manifest["goal"] = agent.goal
        if hasattr(agent, "backstory"):
            manifest["backstory"] = agent.backstory
        if hasattr(agent, "llm"):
            if hasattr(agent.llm, "model"):
                manifest["model"] = agent.llm.model
            model_settings = {}
            if hasattr(agent.llm, "max_tokens"):
                model_settings["max_tokens"] = agent.llm.max_tokens
            if hasattr(agent.llm, "temperature"):
                model_settings["temperature"] = agent.llm.temperature
            if model_settings:
                manifest["model_settings"] = model_settings
        if hasattr(agent, "allow_delegation"):
            manifest["handoffs"] = {"allow_delegation": agent.allow_delegation}
        code_execution_permissions = {}
        if hasattr(agent, "allow_code_execution"):
            manifest["code_execution_permissions"] = {"allow_code_execution": agent.allow_code_execution}
        if hasattr(agent, "code_execution_mode"):
            manifest["code_execution_permissions"] = {"code_execution_mode": agent.code_execution_mode}
        if code_execution_permissions:
            manifest["code_execution_permissions"] = code_execution_permissions
        if hasattr(agent, "max_iter"):
            manifest["max_iterations"] = agent.max_iter
        if hasattr(agent, "tools"):
            manifest["tools"] = self._get_agent_tools(agent.tools)

        span._set_ctx_item(AGENT_MANIFEST, manifest)

    def _get_agent_tools(self, tools):
        if not tools or not isinstance(tools, list):
            return []
        formatted_tools = []
        for tool in tools:
            tool_dict = {}
            if hasattr(tool, "name"):
                tool_dict["name"] = tool.name
            if hasattr(tool, "description"):
                tool_dict["description"] = tool.description
            formatted_tools.append(tool_dict)
        return formatted_tools

    def _llmobs_set_tags_flow(self, span, args, kwargs, response):
        inputs = get_argument_value(args, kwargs, 0, "inputs", optional=True) or {}
        span._set_ctx_items({NAME: span.name or "CrewAI Flow", INPUT_VALUE: inputs, OUTPUT_VALUE: str(response)})
        return

    def _llmobs_set_tags_flow_method(self, span, args, kwargs, response):
        flow_instance = kwargs.pop("_dd.instance", None)
        initial_flow_state = kwargs.pop("_dd.initial_flow_state", {})
        input_dict = {
            "args": [safe_json(arg) for arg in args[2:]],
            "kwargs": {k: safe_json(v) for k, v in kwargs.items()},
            "flow_state": initial_flow_state,
        }
        span_links = (
            self._flow_span_to_method_to_span_dict.get(span._parent, {}).get(span.name, {}).get("span_links", [])
        )
        if span.name in getattr(flow_instance, "_start_methods", []) and span._parent is not None:
            span_links.append(
                _SpanLink(
                    span_id=str(span._parent.span_id),
                    trace_id=format_trace_id(span.trace_id),
                    attributes={"from": "input", "to": "input"},
                )
            )

        if span.name in getattr(flow_instance, "_routers", []):
            # For router methods the downstream trigger is the router's result, not the method name.
            # We store the span info keyed by that result so it can be linked to future listener spans.
            span_dict = self._flow_span_to_method_to_span_dict.get(span._parent, {}).setdefault(str(response), {})
            span_dict.update({"span_id": str(span.span_id), "trace_id": format_trace_id(span.trace_id)})

        span._set_ctx_items(
            {
                NAME: span.name or "Flow Method",
                INPUT_VALUE: input_dict,
                OUTPUT_VALUE: str(response),
                SPAN_LINKS: span_links,
            }
        )
        return

    def llmobs_set_span_links_on_flow(self, flow_span, args, kwargs, flow_instance):
        if not self.llmobs_enabled:
            return
        try:
            self._llmobs_set_span_link_on_flow(flow_span, args, kwargs, flow_instance)
        except (KeyError, AttributeError):
            pass

    def _llmobs_set_span_link_on_flow(self, flow_span, args, kwargs, flow_instance):
        """
        Set span links for the next queued listener method(s) in a CrewAI flow.

        Notes:
        - trigger_method is either a method name or router result, which trigger normal/router listeners respectively.
          We skip if trigger_method is a router method name because we use the router result to link triggered listeners
        - AND conditions:
            - temporary output->output span links added by default for all trigger methods
            - once all trigger methods have run for the listener, remove temporary output->output links and
              add span links from trigger spans to listener span
        """
        trigger_method = get_argument_value(args, kwargs, 0, "trigger_method", optional=True)
        if not trigger_method:
            return
        flow_methods_to_spans = self._flow_span_to_method_to_span_dict.get(flow_span, {})
        trigger_span_dict = flow_methods_to_spans.get(trigger_method)
        if not trigger_span_dict or trigger_method in getattr(flow_instance, "_routers", []):
            # For router methods the downstream trigger listens for the router's result, not the router method name
            # Skip if trigger_method represents a router method name instead of the router's results
            return
        listeners = getattr(flow_instance, "_listeners", {})
        triggered = False
        for listener_name, (condition_type, listener_triggers) in listeners.items():
            if trigger_method not in listener_triggers:
                continue
            span_dict = flow_methods_to_spans.setdefault(listener_name, {})
            span_links = span_dict.setdefault("span_links", [])
            if condition_type != "AND":
                triggered = True
                span_links.append(
                    _SpanLink(
                        span_id=str(trigger_span_dict["span_id"]),
                        trace_id=format_trace_id(flow_span.trace_id),
                        attributes={"from": "output", "to": "input"},
                    )
                )
                continue
            if any(
                flow_methods_to_spans.get(_trigger_method, {}).get("span_id") is None
                for _trigger_method in listener_triggers
            ):  # skip if not all trigger methods have run (span ID must exist) for AND listener
                continue
            triggered = True
            for method in listener_triggers:
                method_span_dict = flow_methods_to_spans.get(method, {})
                span_links.append(
                    _SpanLink(
                        span_id=str(method_span_dict["span_id"]),
                        trace_id=format_trace_id(flow_span.trace_id),
                        attributes={"from": "output", "to": "input"},
                    )
                )
                flow_span_span_links = flow_span._get_ctx_item(SPAN_LINKS) or []
                # Remove temporary output->output link since the AND has been triggered
                span_links_minus_tmp_output_links = [
                    link for link in flow_span_span_links if link["span_id"] != str(method_span_dict["span_id"])
                ]
                flow_span._set_ctx_item(SPAN_LINKS, span_links_minus_tmp_output_links)

        if triggered is False:
            flow_span_span_links = flow_span._get_ctx_item(SPAN_LINKS) or []
            flow_span_span_links.append(
                _SpanLink(
                    span_id=str(trigger_span_dict["span_id"]),
                    trace_id=format_trace_id(flow_span.trace_id),
                    attributes={"from": "output", "to": "output"},
                )
            )
            flow_span._set_ctx_item(SPAN_LINKS, flow_span_span_links)
        return

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
        if not self.llmobs_enabled:
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
        span_links: List[_SpanLink] = []

        if isinstance(getattr(queued_task, "context", None), Iterable):
            for finished_task in queued_task.context:
                finished_task_id = getattr(finished_task, "id", "")
                finished_task_node = self._crews_to_tasks.get(crew_id, {}).get(str(finished_task_id), {})
                finished_task_span_id = finished_task_node.get("span_id")
                span_links.append(
                    _SpanLink(
                        span_id=finished_task_span_id,
                        trace_id=format_trace_id(span.trace_id),
                        attributes={"from": "output", "to": "input"},
                    )
                )
            queued_task_node["span_links"] = span_links
            return
        if not finished_task_outputs and not is_planning_crew_instance:
            queued_task_node["span_links"] = [
                _SpanLink(
                    span_id=str(span.span_id) if span else ROOT_PARENT_ID,
                    trace_id=format_trace_id(span.trace_id),
                    attributes={"from": "input", "to": "input"},
                )
            ]
            return
        if is_planning_crew_instance and self._crews_to_task_span_ids.get(crew_id, []):
            planning_task_span_id = self._crews_to_task_span_ids[crew_id][-1]
            queued_task_node["span_links"] = [
                _SpanLink(
                    span_id=planning_task_span_id if span else ROOT_PARENT_ID,
                    trace_id=format_trace_id(span.trace_id),
                    attributes={"from": "output", "to": "input"},
                )
            ]
            return

        finished_task_spans = self._crews_to_task_span_ids.get(crew_id, [])
        num_tasks_to_link = min(len(finished_task_outputs), len(finished_task_spans))
        for i in range(1, num_tasks_to_link + 1):  # Iterate backwards through last n finished tasks
            finished_task_span_id = finished_task_spans[-i]
            span_links.append(
                _SpanLink(
                    span_id=finished_task_span_id,
                    trace_id=format_trace_id(span.trace_id),
                    attributes={"from": "output", "to": "input"},
                )
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
