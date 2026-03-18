from dataclasses import dataclass
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
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

    provider: str = event_field()
    model: Optional[str] = event_field(default=None)
    llmobs_integration: "BaseLLMIntegration" = event_field()
    request_kwargs: dict[str, Any] = event_field(default_factory=dict)
    submit_to_llmobs: bool = event_field(default=False)
    instance: Optional[Any] = event_field(default=None)
    response: Optional[Any] = event_field(default=None)
    is_chat: Optional[bool] = event_field(default=None)
    operation: str = event_field(default="")

    def __post_init__(self) -> None:
        self.span_name = f"{self.component}.request"
        # span_type is only LLM when LLMObs is enabled and submit_to_llmobs is True
        if not (self.submit_to_llmobs and self.llmobs_integration.llmobs_enabled):
            self.__dict__["span_type"] = None
