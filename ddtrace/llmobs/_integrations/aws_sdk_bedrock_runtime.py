from typing import Any
from typing import Optional

from ddtrace.contrib._events.aws_sdk_bedrock_runtime import BedrockBidirectionalStreamEvent
from ddtrace.llmobs._integrations._aws_sdk_bedrock_runtime import SonicState
from ddtrace.llmobs._integrations.base import BaseLLMIntegration


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
        return self._start_audio_span(name, kind, model, session_id, parent, start_ns, "amazon")


def on_bidirectional_stream(event: BedrockBidirectionalStreamEvent) -> None:
    integration = AwsSdkBedrockRuntimeIntegration(event.integration_config)
    if integration.llmobs_enabled and event.model == "amazon.nova-2-sonic-v1:0":
        event.observer = SonicState(integration, event.model, event.parent)
