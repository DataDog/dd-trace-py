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
    def _annotate_integration_tag(self, span: Any) -> None: ...
    def _stamp_llmobs_span_kind_at_start(self, span: Any, operation_id: str = "", **kwargs: Any) -> None: ...
    def llmobs_set_tags(
        self,
        span: Any,
        args: list,
        kwargs: dict,
        response: Optional[Any] = None,
        operation: str = "",
        set_apm_shadow_tags: bool = True,
    ) -> None: ...


class LlmApmTagger(Protocol):
    """Provider-specific APM span tagging owned by the contrib integration.

    Lets the APM span carry model, provider, and token usage tags without the llmobs product
    package being loaded. When set on an LlmRequestEvent it takes precedence over the equivalent
    LLMObsIntegrationLike methods.
    """

    def get_base_url(self, instance: Any) -> Optional[str]: ...
    def set_base_span_tags(self, span: Any, model: Optional[str], instance: Any) -> None: ...
    def set_apm_shadow_tags(self, span: Any, response: Any, llmobs_enabled: bool) -> None: ...


@dataclass
class LlmRequestEvent(TracingEvent):
    """LLM request event for all LLM integrations.

    Carries everything needed for span creation and LLMObs tag extraction.
    Provider-specific APM tagging lives in apm_tagger when the integration provides one, otherwise
    in the LLMObs integration class methods (_set_base_span_tags, llmobs_set_tags).
    """

    event_name = "llm.request"
    span_kind = SpanKind.CLIENT

    provider: str = event_field()
    model: Optional[str] = event_field(default=None)
    # None when the llmobs product package isn't loaded; apm_tagger then does all the span tagging.
    llmobs_integration: Optional[LLMObsIntegrationLike] = event_field(default=None)
    apm_tagger: Optional[LlmApmTagger] = event_field(default=None)
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
