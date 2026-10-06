from typing import Any
from typing import Optional

from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.trace import Span


class ElevenLabsIntegration(BaseLLMIntegration):
    _integration_name = "elevenlabs"

    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        span.set_tag("elevenlabs.agent_id", kwargs.get("agent_id", ""))

    def _llmobs_set_tags(
        self,
        span: Span,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        _annotate_llmobs_span_data(span, **kwargs)

    def annotate_voice_span(
        self,
        span: Span,
        kind: str,
        name: str,
        session_id: str,
        metadata: dict[str, Any],
        parent: Optional[Span] = None,
        **kwargs: Any,
    ) -> None:
        if parent is not None:
            kwargs["parent_id"] = str(parent.span_id)
            kwargs["trace_id"] = get_llmobs_trace_id(parent)
        if kind == "llm":
            # Context usage identifies a configured underlying model, not per-response
            # model attribution or token usage. ElevenLabs is the hosted agent provider.
            kwargs["model_provider"] = "elevenlabs"
        _annotate_llmobs_span_data(
            span,
            kind=kind,
            name=name,
            session_id=session_id,
            metadata=metadata,
            **kwargs,
        )
