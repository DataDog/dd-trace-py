from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import INPUT_VALUE
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import OUTPUT_VALUE
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.trace import Span


log = get_logger(__name__)


class LlamaIndexIntegration(BaseLLMIntegration):
    _integration_name = "llama_index"

    def _set_base_span_tags(
        self,
        span: Span,
        **kwargs: Dict[str, Any],
    ) -> None:
        provider = kwargs.get("provider")
        if provider is not None:
            span._set_tag_str("llama_index.request.provider", str(provider))

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        span_kind = "workflow"

        input_value = kwargs.get("_dd_query_input", "")

        output_value = ""
        if response is not None:
            output_value = str(response)

        span._set_ctx_items(
            {
                SPAN_KIND: span_kind,
                MODEL_NAME: span.get_tag("llama_index.request.model") or "",
                MODEL_PROVIDER: "llama_index",
                INPUT_VALUE: input_value,
                OUTPUT_VALUE: output_value,
                METADATA: {},
            }
        )
