from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace.pin import Pin
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span


class LiteLLMIntegration(BaseLLMIntegration):
    _integration_name = "litellm"

    def _set_base_span_tags(
        self, span: Span, model: Optional[str] = None, host: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if "host" in kwargs.get("metadata", {}).get("headers", {}):
            host = kwargs["metadata"]["headers"]["host"]
        if model is not None:
            span.set_tag_str("litellm.request.model", model)
        if host is not None:
            span.set_tag_str("litellm.request.host", host)
