from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Optional
from typing import Protocol

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field


class LLMObsIntegrationLike(Protocol):
    """Structural type for the LLMObs integration object an LlmRequestEvent carries.

    Matches ddtrace.llmobs._integrations.base.BaseLLMIntegration without importing it, since
    ddtrace.contrib must not depend on the llmobs product package.
    """

    integration_config: Any
    llmobs_enabled: bool

    def _set_base_span_tags(self, span: Any, **kwargs: Any) -> None: ...
    def _get_base_url(self, **kwargs: Any) -> Optional[str]: ...
    def _is_instrumented_proxy_url(self, base_url: Optional[str] = None) -> bool: ...
    def _annotate_integration_tag(self, span: Any) -> None: ...
    def _stamp_llmobs_span_kind_at_start(self, span: Any, operation_id: str = "", **kwargs: Any) -> None: ...
    def llmobs_set_tags(
        self,
        span: Any,
        args: list,
        kwargs: dict,
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None: ...


@dataclass
class LlmRequestEvent(TracingEvent):
    """LLM request event for all LLM integrations.

    Carries everything needed for span creation and LLMObs tag extraction.
    Provider-specific logic stays in the integration class methods
    (_set_base_span_tags, llmobs_set_tags).
    """

    event_name = "llm.request"
    span_kind = SpanKind.CLIENT

    provider: str = event_field()
    model: Optional[str] = event_field(default=None)
    llmobs_integration: LLMObsIntegrationLike = event_field()
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
        self.span_type = SpanTypes.LLM if (self.submit_to_llmobs and self.llmobs_integration.llmobs_enabled) else None
