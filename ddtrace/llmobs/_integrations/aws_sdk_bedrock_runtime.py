from typing import Any

from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.trace import Span


class AwsSdkBedrockRuntimeIntegration(BaseLLMIntegration):
    _integration_name = "aws_sdk_bedrock_runtime"

    def _set_base_span_tags(self, span: Span, **kwargs: Any) -> None:
        span._set_attribute("bedrock.request.model", kwargs.get("model", ""))
        span._set_attribute("bedrock.request.model_provider", "amazon")

    def start_span(self, name: str, kind: str, model: str, session_id: str, parent: Any, start_ns: int) -> Span:
        span = self.trace(
            name, span_name=name, model=model, submit_to_llmobs=True, activate=False, parent_context=parent
        )
        span.start_ns = start_ns
        # AIDEV-NOTE: duplex callbacks do not run under the turn's active context.
        # Stamp identity before optional message enrichment, as in OpenAI Realtime.
        identity: dict[str, Any] = {}
        if isinstance(parent, Span):
            identity = {
                "parent_id": str(parent.span_id),
                "trace_id": get_llmobs_trace_id(parent) or format_trace_id(parent.trace_id),
            }
        _annotate_llmobs_span_data(
            span,
            name=name,
            kind=kind,
            session_id=session_id,
            model_name=model if kind == "llm" else None,
            model_provider="amazon" if kind == "llm" else None,
            **identity,
        )
        return span
