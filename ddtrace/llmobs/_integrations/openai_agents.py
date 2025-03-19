from typing import Any
from typing import Dict
from typing import Iterable
from typing import List
from typing import Optional

from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._constants import INPUT_MESSAGES
from ddtrace.llmobs._constants import METADATA
from ddtrace.llmobs._constants import METRICS
from ddtrace.llmobs._constants import MODEL_NAME
from ddtrace.llmobs._constants import MODEL_PROVIDER
from ddtrace.llmobs._constants import NAME
from ddtrace.llmobs._constants import OUTPUT_MESSAGES
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._integrations.utils import extract_message_from_part_google
from ddtrace.llmobs._integrations.utils import get_llmobs_metrics_tags
from ddtrace.llmobs._integrations.utils import get_system_instructions_from_google_model
from ddtrace.llmobs._integrations.utils import llmobs_get_metadata_google
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import _get_nearest_llmobs_ancestor
from ddtrace.llmobs._utils import _get_span_name
from ddtrace.trace import Pin
from ddtrace.trace import Span


class OpenAIAgentsIntegration(BaseLLMIntegration):
    _integration_name = "openai_agents"

    def _llmobs_set_tags(
        self,
        span: Span,
        args: List[Any],
        kwargs: Dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        pass

        raw_oai_span = kwargs.get("raw_oai_span")

    def trace(
        self,
        pin: Pin,
        operation_id: str,
        submit_to_llmobs: bool = False,
        span_name: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> Span:
        span = super().trace(pin, operation_id, submit_to_llmobs, span_name, **kwargs)
        llmobs_span_kind = kwargs.get("llmobs_span_kind")
        span._set_ctx_item(SPAN_KIND, llmobs_span_kind)
        if llmobs_span_kind == "llm":
            parent = _get_nearest_llmobs_ancestor(span)
            if parent and parent._get_ctx_item(SPAN_KIND) == "agent" and _get_span_name(parent):
                span._set_ctx_item(NAME, _get_span_name(parent) + " (LLM)")
        return span
