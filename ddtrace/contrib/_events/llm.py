from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.internal.core.events import event_field


class LlmEvents(Enum):
    LLM_REQUEST = "llm.request"


@dataclass
class LlmRequestEvent(TracingEvent):
    """LLM request event for all LLM integrations.

    Carries everything needed for span creation and for the products that tag the span
    through the SpanLifecycleEvents dispatched for llm.request.
    """

    event_name = LlmEvents.LLM_REQUEST.value
    span_kind = SpanKind.CLIENT

    # None when the request has no model provider (for example LangChain chains and tools).
    provider: Optional[str] = event_field()
    model: Optional[str] = event_field(default=None)
    # Positional request arguments, for integrations whose wrapped calls accept them.
    request_args: tuple[Any, ...] = event_field(default=())
    request_kwargs: dict[str, Any] = event_field(default_factory=dict)
    submit_to_llmobs: bool = event_field(default=False)
    instance: Optional[Any] = event_field(default=None)
    response: Optional[Any] = event_field(default=None)
    operation: str = event_field(default="")
    # Overrides the default "<component>.request" span name.
    span_name: Optional[str] = event_field(default=None)

    # Override ClassVar from TracingEvent with an instance field so products can set the
    # span_type per request (for example LLMObs, when the request is submitted to it).
    span_type: Optional[str] = field(init=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.operation_name = self.span_name or f"{self.component}.request"
        self.span_type = None
