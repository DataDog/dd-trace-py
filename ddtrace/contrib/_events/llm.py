from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field


if TYPE_CHECKING:
    from ddtrace.llmobs._integrations.base import BaseLLMIntegration


class LlmEvents(Enum):
    LLM_REQUEST = "llm.request"
    # Dispatched by LlmTracingSubscriber with the ExecutionContext so products can
    # take part in the span lifecycle without the contrib importing them:
    # SPAN_STARTING fires before the span is created (the only point where
    # event.span_type can still change), SPAN_STARTED right after, and
    # SPAN_FINISHING before the span is finished.
    SPAN_STARTING = "llm.request.span_starting"
    SPAN_STARTED = "llm.request.span_started"
    SPAN_FINISHING = "llm.request.span_finishing"


@dataclass
class LlmRequestEvent(TracingEvent):
    """LLM request event for all LLM integrations.

    Carries everything needed for span creation and LLMObs tag extraction.
    Provider-specific logic stays in the integration class methods
    (_set_base_span_tags, llmobs_set_tags).
    """

    event_name = LlmEvents.LLM_REQUEST.value
    span_kind = SpanKind.CLIENT

    provider: str = event_field()
    model: Optional[str] = event_field(default=None)
    # Integrations that have moved to LlmEvents subscribers leave this unset.
    llmobs_integration: Optional[BaseLLMIntegration] = event_field(default=None)
    request_kwargs: dict[str, Any] = event_field(default_factory=dict)
    submit_to_llmobs: bool = event_field(default=False)
    instance: Optional[Any] = event_field(default=None)
    response: Optional[Any] = event_field(default=None)
    operation: str = event_field(default="")

    # Override ClassVar from TracingEvent with an instance field so span_type
    # can be determined dynamically per-request based on LLMObs state.
    span_type: Optional[str] = field(init=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.operation_name = f"{self.component}.request"
        self.span_type = (
            SpanTypes.LLM
            if (
                self.submit_to_llmobs and self.llmobs_integration is not None and self.llmobs_integration.llmobs_enabled
            )
            else None
        )
