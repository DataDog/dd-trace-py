"""Observer handoff for an independently consumed Bedrock duplex stream."""

from dataclasses import dataclass
from typing import Any
from typing import Optional
from typing import Protocol

from ddtrace.internal.core.events import Event
from ddtrace.internal.settings.integration import IntegrationConfig


class BedrockStreamObserver(Protocol):
    def observe(self, event: Any, outbound: bool = False) -> None: ...
    def finish(self, error: Any = None) -> None: ...
    def finish_error(self, error: Any = None) -> None: ...


@dataclass
class BedrockBidirectionalStreamEvent(Event):
    event_name = "aws_sdk_bedrock_runtime.bidirectional_stream"
    integration_config: IntegrationConfig
    model: str
    parent: Any
    observer: Optional[BedrockStreamObserver] = None
