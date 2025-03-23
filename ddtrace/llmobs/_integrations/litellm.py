from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.trace import Span
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class LiteLLMIntegration(BaseLLMIntegration):
    _integration_name = "litellm"

    def _set_base_span_tags(
        self, span: Span, model: Optional[str] = None, **kwargs: Dict[str, Any]
    ) -> None:
        if model is not None:
            span.set_tag_str("litellm.request.model", model)
