from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import TracingEvent
from ddtrace.internal.core.events import event_field


if TYPE_CHECKING:
    from ddtrace.llmobs._integrations.base import BaseLLMIntegration


@dataclass
class LlmRequestEvent(TracingEvent):
    """LLM request event for all LLM integrations.

    Carries everything needed for span creation and LLMObs tag extraction.
    Provider-specific logic stays in the integration class methods
    (_set_base_span_tags, llmobs_set_tags).
    """

    event_name = "llm.request"
    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.LLM

    integration_name: str = event_field()
    provider: str = event_field()
    model: str = event_field()
    integration: "BaseLLMIntegration" = event_field()
    request_kwargs: Dict[str, Any] = event_field(default_factory=dict)
    submit_to_llmobs: bool = event_field(default=False)
    base_tag_kwargs: Optional[Dict[str, Any]] = event_field(default_factory=dict)

    def __post_init__(self):
        self.set_component(self.integration_name)
        if not self.__dict__.get("span_name"):
            self.set_span_name("{}.request".format(self.integration_name))
        # span_type is only LLM when LLMObs is enabled and submit_to_llmobs is True
        if not (self.submit_to_llmobs and self.integration.llmobs_enabled):
            self.__dict__["span_type"] = None
