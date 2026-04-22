from dataclasses import dataclass
from typing import Protocol

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_OPERATION
from ddtrace.internal.constants import MESSAGING_SYSTEM
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.propagation.http import HTTPPropagator


class _MessagingEvent(Protocol):
    operation_name: str
    tags: dict[str, str]
    messaging_system: str
    destination: str


def _init_messaging_event(event: _MessagingEvent, operation: str, direction: SpanDirection) -> None:
    """Set the operation name and common messaging tags shared by all messaging events."""
    event.operation_name = schematize_messaging_operation(
        f"{event.messaging_system}.{operation}",
        provider=event.messaging_system,
        direction=direction,
    )
    event.tags[MESSAGING_SYSTEM] = event.messaging_system
    event.tags[MESSAGING_DESTINATION_NAME] = event.destination
    event.tags[MESSAGING_OPERATION] = operation


@dataclass
class MessagingPublishEvent(TracingEvent):
    """Event for messaging publish/produce operations.

    Sets _emit_scoped_event so integrations can subscribe to
    ``context.started.messaging.publish.<component>`` without guards.

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

    event_name = "messaging.publish"
    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER
    _emit_scoped_event = True

    messaging_system: str = event_field()
    destination: str = event_field()
    #: The message's outbound header dict — modified in-place by subscribers.
    headers: dict[str, str] = event_field(default_factory=dict)
    body: bytes = event_field(default=b"")
    distributed_tracing_enabled: bool = event_field(default=True)

    def __post_init__(self) -> None:
        _init_messaging_event(self, "publish", SpanDirection.OUTBOUND)


@dataclass
class MessagingConsumeEvent(TracingEvent):
    """Event for messaging consume/receive operations.

    Pass ``headers`` as a plain ``dict[str, str]`` decoded from the
    wire format by the integration patch.  Distributed-tracing context is
    extracted from ``headers`` during construction (via ``HTTPPropagator``)
    and stored as ``distributed_context`` so the span is parented correctly.

    DSM reads ``headers``, ``body``, and ``destination`` directly from the
    event — it does not require an APM span and works when APM tracing is
    disabled.
    """

    event_name = "messaging.consume"
    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER
    _emit_scoped_event = True

    messaging_system: str = event_field()
    destination: str = event_field()
    headers: dict[str, str] = event_field(default_factory=dict)
    body: bytes = event_field(default=b"")
    distributed_tracing_enabled: bool = event_field(default=True)
    operation: str = event_field(default="receive")
    # If set, overrides the operation token used when building operation_name while
    # leaving the ``messaging.operation`` tag equal to ``operation``.  Use this
    # when the method name (e.g. "get") differs from the semantic operation
    # category (e.g. "receive").
    span_operation: str = event_field(default="")

    def __post_init__(self) -> None:
        span_op = self.span_operation or self.operation
        _init_messaging_event(self, span_op, SpanDirection.PROCESSING)
        # The tag must reflect the semantic operation, not the span operation override.
        self.tags[MESSAGING_OPERATION] = self.operation
        if self.distributed_tracing_enabled and self.headers:
            self.distributed_context = HTTPPropagator.extract(self.headers)
            self.use_active_context = False


@dataclass
class MessagingActionEvent(TracingEvent):
    """Event for messaging acknowledgement operations (ack, nack, reject)."""

    event_name = "messaging.action"
    span_kind = SpanKind.INTERNAL
    span_type = SpanTypes.WORKER
    _emit_scoped_event = True

    messaging_system: str = event_field()
    destination: str = event_field()
    operation: str = event_field()

    def __post_init__(self) -> None:
        _init_messaging_event(self, self.operation, SpanDirection.PROCESSING)
