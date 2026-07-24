from dataclasses import dataclass
from enum import Enum

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


class MessagingEvents(str, Enum):
    PUBLISH = "messaging.publish"
    CONSUME = "messaging.consume"
    ACTION = "messaging.action"


class AioPikaEvents(str, Enum):
    PUBLISH = f"{MessagingEvents.PUBLISH.value}.aio_pika"
    CONSUME = f"{MessagingEvents.CONSUME.value}.aio_pika"
    ACTION = f"{MessagingEvents.ACTION.value}.aio_pika"


@dataclass
class MessagingPublishEvent(TracingEvent):
    """Event for messaging publish/produce operations.

    Pass ``headers`` as the message's own mutable ``dict[str, str]``.
    After context entry, ``MessagingTracingSubscriber`` injects distributed-
    tracing context directly into this dict; the DSM subscriber (if enabled)
    then encodes its pathway into the same dict.  Because the integration
    passes the real message headers object by reference, no write-back is
    needed — the injected headers are already on the message.

    This design is intentionally integration-agnostic: any messaging library
    can use this event by providing a ``dict[str, str]`` view of its outbound
    headers and arranging for that dict to be written back to the wire format
    if the library does not share a Python reference.
    """

    event_name = MessagingEvents.PUBLISH.value
    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER

    messaging_system: str = event_field()
    destination: str = event_field()
    routing_key: str = event_field(default="")
    #: The message's outbound header dict — modified in-place by subscribers.
    headers: dict[str, str] = event_field(default_factory=dict)
    body: bytes = event_field(default=b"")
    distributed_tracing_enabled: bool = event_field(default=False)

    def __post_init__(self) -> None:
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]
            f"{self.messaging_system}.publish",
            provider=self.messaging_system,
            direction=SpanDirection.OUTBOUND,
        )


@dataclass
class MessagingConsumeEvent(TracingEvent):
    """Event for messaging consume/receive operations.

    Pass ``headers`` as a plain ``dict[str, str]`` decoded from the wire format
    by the integration patch.  The messaging subscriber extracts distributed
    context from those headers before starting the span.

    DSM reads ``headers``, ``body``, and ``destination`` directly from the
    event — it does not require an APM span and works when APM tracing is
    disabled.
    """

    event_name = MessagingEvents.CONSUME.value
    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER

    messaging_system: str = event_field()
    destination: str = event_field()
    headers: dict[str, str] = event_field(default_factory=dict)
    body: bytes = event_field(default=b"")
    distributed_tracing_enabled: bool = event_field(default=False)
    operation: str = event_field(default="receive")
    start_ns: int = event_field(default=0)
    # If set, overrides the operation token used when building operation_name while
    # leaving the ``messaging.operation`` tag equal to ``operation``.  Use this
    # when the method name (e.g. "get") differs from the semantic operation
    # category (e.g. "receive").
    span_operation: str = event_field(default="")

    def __post_init__(self) -> None:
        span_op = self.span_operation or self.operation
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]
            f"{self.messaging_system}.{span_op}",
            provider=self.messaging_system,
            direction=SpanDirection.PROCESSING,
        )


@dataclass
class MessagingActionEvent(TracingEvent):
    """Event for messaging acknowledgement operations (ack, nack, reject)."""

    event_name = MessagingEvents.ACTION.value
    span_kind = SpanKind.INTERNAL
    span_type = SpanTypes.WORKER

    messaging_system: str = event_field()
    destination: str = event_field()
    operation: str = event_field()

    def __post_init__(self) -> None:
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]
            f"{self.messaging_system}.{self.operation}",
            provider=self.messaging_system,
            direction=SpanDirection.PROCESSING,
        )
