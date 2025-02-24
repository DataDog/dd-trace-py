from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import get_llmobs_metrics_tags
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.trace import Span


log = get_logger(__name__)




class CrewAIIntegration(BaseLLMIntegration):
    _integration_name = "crewai"
    _traces_to_task_span_ids = {}
    _traces_to_tasks = {}
    _agents_to_span_ids = {}
    _traces_to_span_links = {}

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """On span start, setup base span tags and span linking."""
        if not self.llmobs_enabled or not self.span_linking_enabled:
           return
        if kwargs.get("operation") == "crew":
            self._traces_to_task_span_ids[span.trace_id] = [str(span.span_id)]
            self._traces_to_tasks[span.trace_id] = {}
            return
        if kwargs.get("operation") == "task":
            self._traces_to_tasks[span.trace_id][kwargs.get("instance_id")] = [str(span.span_id)]
            prev_task_span_ids = self._traces_to_task_span_ids[span.trace_id]
            if len(prev_task_span_ids) == 1:  # first task in crew execution
                link_type = {"from": "input", "to": "input"}
            else:
                link_type = {"from": "output", "to": "input"}
            prev_span_id = prev_task_span_ids[-1]
            span_link = {
                "span_id": prev_span_id,
                "trace_id": "{:x}".format(span.trace_id),
                "attributes": link_type,
            }
            curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
            span._set_ctx_item(SPAN_LINKS, curr_span_links + [span_link])
            self._traces_to_task_span_ids[span.trace_id].append(str(span.span_id))
            span._set_ctx_item("_ml_obs.task_id", kwargs.get("instance_id"))
        elif kwargs.get("operation") == "agent":
            task_span = _get_nearest_llmobs_ancestor(span)
            if task_span is None:
                return
            task_id = task_span._get_ctx_item("_ml_obs.task_id")
            self._traces_to_tasks[span.trace_id][task_id].append(str(span.span_id))
            span_link = {
                "span_id": str(task_span.span_id),
                "trace_id": "{:x}".format(span.trace_id),
                "attributes": {"from": "input", "to": "input"},
            }
            curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
            span._set_ctx_item(SPAN_LINKS, curr_span_links + [span_link])


    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        if operation == "crew":
            self._llmobs_set_metadata_crew(span, args, kwargs, response)
            self._traces_to_task_span_ids.pop(span.trace_id, None)
            self._traces_to_tasks.pop(span.trace_id, None)
        elif operation == "task":
            self._llmobs_set_metadata_task(span, args, kwargs, response)
        elif operation == "agent":
            self._llmobs_set_metadata_agent(span, args, kwargs, response)
        elif operation == "tool":
            self._llmobs_set_metadata_tool(span, args, kwargs, response)

        span._set_ctx_items(
            {
                SPAN_KIND: "workflow" if operation == "crew" else operation,
            }
        )

    def _llmobs_set_metadata_crew(self, span, args, kwargs, response):
        last_task_span_id = self._traces_to_task_span_ids[span.trace_id][-1]
        span_link = {
            "span_id": last_task_span_id,
            "trace_id": "{:x}".format(span.trace_id),
            "attributes": {"from": "output", "to": "output"},
        }
        curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
        span._set_ctx_item(SPAN_LINKS, curr_span_links + [span_link])
        span._set_ctx_item(INPUT_VALUE, args)
        span._set_ctx_item(OUTPUT_VALUE, response.raw)

    def _llmobs_set_metadata_task(self, span, args, kwargs, response):
        task_id = span._get_ctx_item("_ml_obs.task_id")
        last_agent_span_id = self._traces_to_tasks[span.trace_id][task_id][-1]
        span_link = {
            "span_id": last_agent_span_id,
            "trace_id": "{:x}".format(span.trace_id),
            "attributes": {"from": "output", "to": "output"},
        }
        curr_span_links = span._get_ctx_item(SPAN_LINKS) or []
        span._set_ctx_item(SPAN_LINKS, curr_span_links + [span_link])
        span._set_ctx_item(INPUT_VALUE, kwargs["instance"].description)
        span._set_ctx_item(OUTPUT_VALUE, response.raw)
        span._set_ctx_item(METADATA, {"expected_output": kwargs["instance"].expected_output})

    def _llmobs_set_metadata_agent(self, span, args, kwargs, response):
        span._set_ctx_item(METADATA,{"description": kwargs["instance"].goal, "backstory": kwargs["instance"].backstory})
        span._set_ctx_item(INPUT_VALUE, {"context": kwargs.get("context"), "input": kwargs.get("task").description})
        span._set_ctx_item(OUTPUT_VALUE, response)

    def _llmobs_set_metadata_tool(self, span, args, kwargs, response):
        span._set_ctx_item(METADATA, {"description": kwargs["instance"].description})
        span._set_ctx_item(INPUT_VALUE, kwargs["input"])
        span._set_ctx_item(OUTPUT_VALUE, response)

    def _llmobs_set_span_link_on_task(self, span, finished_tasks):
        if not self.llmobs_enabled or not self.span_linking_enabled:
            return
        # for task in finished_tasks:
        #     span_link = {
        #         "span_id": self._traces_to_task_span_ids[span.trace_id],
        #         "trace_id": "{:x}".format(span.trace_id),
        #         "attributes": link_type,
        #     }
