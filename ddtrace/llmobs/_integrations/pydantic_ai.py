from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._llmobs import LLMObs
from ddtrace.trace import Span
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import INPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import OUTPUT_TOKENS_METRIC_KEY
from ddtrace.llmobs._constants import TOTAL_TOKENS_METRIC_KEY
from ddtrace.llmobs._utils import _get_attr, _get_nearest_llmobs_ancestor


# in some cases, PydanticAI uses a different provider name than what we expect
PYDANTIC_AI_SYSTEM_TO_PROVIDER = {
    "google-gla": "google",
    "google-vertex": "google",
}


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"
    _running_agents = {} # dictionary mapping agent span ID to tool span ID(s)
    _latest_agent = None # str representing the span ID of the latest agent that was started

    def _set_base_span_tags(self, span: Span, model: Optional[str] = None, **kwargs) -> None:
        if model:
            span.set_tag("pydantic_ai.request.model", getattr(model, "model_name", ""))
            system = getattr(model, "system", None)
            if system:
                system = PYDANTIC_AI_SYSTEM_TO_PROVIDER.get(system, system)
                span.set_tag("pydantic_ai.request.provider", system)
    
    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span_links = self._get_span_links(span)

        metrics = self.extract_usage_metrics(response)
        span._set_ctx_items(
            {SPAN_KIND: span._get_ctx_item(SPAN_KIND), SPAN_LINKS: span_links, MODEL_NAME: span.get_tag("pydantic_ai.request.model") or "", MODEL_PROVIDER: span.get_tag("pydantic_ai.request.provider") or "", METRICS: metrics}
        )
    
    def extract_usage_metrics(self, response: Any) -> Dict[str, Any]:
        usage = None
        try:
            usage = response.usage()
        except Exception:
            return {}
        if usage is None:
            return {}

        prompt_tokens = _get_attr(usage, "request_tokens", 0)
        completion_tokens = _get_attr(usage, "response_tokens", 0)
        total_tokens = _get_attr(usage, "total_tokens", 0)
        return {
            INPUT_TOKENS_METRIC_KEY: prompt_tokens,
            OUTPUT_TOKENS_METRIC_KEY: completion_tokens,
            TOTAL_TOKENS_METRIC_KEY: total_tokens or (prompt_tokens + completion_tokens),
        }

    def _get_span_links(self, span: Span) -> List[Dict[str, Any]]:
        span_kind = span._get_ctx_item(SPAN_KIND)
        span_links = []
        if span_kind == "agent":
            for tool_span_id in self._running_agents[span.span_id]:
                span_links.append({
                    "span_id": str(tool_span_id),
                    "trace_id": format_trace_id(span.trace_id),
                    "attributes": {"from": "output", "to": "output"},
                })
        elif span_kind == "tool":
            ancestor = _get_nearest_llmobs_ancestor(span)
            if ancestor:
                span_links.append({
                    "span_id": str(ancestor.span_id),
                    "trace_id": format_trace_id(ancestor.trace_id),
                    "attributes": {"from": "input", "to": "input"},
                })
        return span_links
    
    def register_span(self, span: Span, kind: str) -> None:
        if kind == "agent":
            self._register_agent(span)
        elif kind == "tool":
            self._register_tool(span)

    def _register_agent(self, span: Span) -> None:
        self._latest_agent = span.span_id
        self._running_agents[span.span_id] = []

    def _register_tool(self, span: Span) -> None:
        self._running_agents[self._latest_agent].append(span.span_id)