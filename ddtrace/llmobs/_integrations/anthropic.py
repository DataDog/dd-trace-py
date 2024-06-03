from typing import Any
from typing import Dict
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.logger import get_logger

from .base import BaseLLMIntegration


log = get_logger(__name__)


API_KEY = "anthropic.request.api_key"
MODEL = "anthropic.request.model"


class AnthropicIntegration(BaseLLMIntegration):
    _integration_name = "anthropic"

    def _set_base_span_tags(
        self,
        span: Span,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> None:
        """Set base level tags that should be present on all Anthropic spans (if they are not None)."""
        if model is not None:
            span.set_tag_str(MODEL, model)
        if api_key is not None:
            if len(api_key) >= 4:
                span.set_tag_str(API_KEY, f"...{str(api_key[-4:])}")
            else:
                span.set_tag_str(API_KEY, api_key)
