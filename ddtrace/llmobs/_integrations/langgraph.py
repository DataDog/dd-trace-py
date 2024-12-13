from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.span import Span

from typing import Any, Dict, List, Optional

from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._constants import SPAN_LINKS
from ddtrace.llmobs._constants import NAME

class LangGraphIntegration(BaseLLMIntegration):
    _integration_name = "langgraph"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ):
        if not self.llmobs_enabled:
            return
        
        span_links = kwargs.get("_dd_from", [])
        span_name = kwargs.get("name", span.name)
        if isinstance(span_links, dict):
            span_links = [span_links]
        
        span._set_ctx_items({
            SPAN_KIND: "agent",
            INPUT_VALUE: args,
            OUTPUT_VALUE: response,
            SPAN_LINKS: [span_link for span_link in span_links if ("trace_id" in span_link and "span_id" in span_link)],
            NAME: span_name,
        })
