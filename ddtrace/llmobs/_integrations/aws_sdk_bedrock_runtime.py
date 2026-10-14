from typing import Any
from typing import Callable
from typing import Optional

from ddtrace.contrib._events.aws_sdk_bedrock_runtime import BedrockBidirectionalStreamEvent
from ddtrace.internal.native._native import Context
from ddtrace.llmobs._context import LLMObsContextProvider
from ddtrace.llmobs._integrations.aws_sdk_bedrock_runtime_utils import SonicState
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.llmobs._utils import _annotate_llmobs_span_data


class AwsSdkBedrockRuntimeIntegration(BaseLLMIntegration):
    _integration_name = "aws_sdk_bedrock_runtime"

    def _set_base_span_tags(self, span: Any, **kwargs: Any) -> None:
        span._set_attribute("bedrock.request.model", kwargs.get("model", ""))
        span._set_attribute("bedrock.request.model_provider", "amazon")

    def _llmobs_set_tags(
        self,
        span: Any,
        args: list[Any],
        kwargs: dict[str, Any],
        response: Optional[Any] = None,
        operation: str = "",
    ) -> None:
        """Duplex state annotates each turn; there is no single SDK response to extract."""

    def start_span(self, name: str, kind: str, model: str, session_id: str, parent: Any, start_ns: int) -> Any:
        span = self.trace(
            name, span_name=name, model=model, submit_to_llmobs=True, activate=False, parent_context=parent
        )
        span.start_ns = start_ns
        _annotate_llmobs_span_data(
            span,
            name=name,
            kind=kind,
            session_id=session_id,
            model_name=model if kind == "llm" else None,
            model_provider="amazon" if kind == "llm" else None,
        )
        return span


def on_bidirectional_stream(
    event: BedrockBidirectionalStreamEvent,
    context_provider: LLMObsContextProvider,
    capture_context: Callable[[], Optional[Context]],
) -> None:
    integration = AwsSdkBedrockRuntimeIntegration(event.integration_config)
    if integration.llmobs_enabled and event.model == "amazon.nova-2-sonic-v1:0":
        context = capture_context()
        if context is not None:
            # Propagated contexts can be shared with the caller; never retain mutable metadata.
            context = Context(trace_id=context.trace_id, span_id=context.span_id, meta=dict(context._meta))
        event.observer = SonicState(integration, event.model, event.parent, context_provider, context)
