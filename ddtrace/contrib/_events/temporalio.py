from dataclasses import dataclass
from enum import Enum
from typing import Any

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_messaging_operation
from ddtrace.internal.schema import schematize_url_operation


class TemporalEvents(Enum):
    START_WORKFLOW = "temporal.start_workflow"
    SIGNAL_WORKFLOW = "temporal.signal_workflow"
    QUERY_WORKFLOW = "temporal.query_workflow"
    RUN_ACTIVITY = "temporal.run_activity"
    FORWARD_CONTEXT = "temporal.context.forward"


TEMPORAL_CONTEXT_HEADER = "_datadog"


@dataclass
class TemporalEvent(TracingEvent):
    input_data: Any = event_field(default=None)
    payload_converter: Any = event_field(default=None)


@dataclass
class TemporalContextForwardEvent(Event):
    event_name = TemporalEvents.FORWARD_CONTEXT.value

    source_input_data: Any
    destination_input_data: Any


@dataclass
class TemporalStartWorkflowEvent(TemporalEvent):
    event_name = TemporalEvents.START_WORKFLOW.value

    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        # Schema functions are selected dynamically and are untyped.
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]
            self.event_name, provider="temporal", direction=SpanDirection.OUTBOUND
        )


@dataclass
class TemporalSignalWorkflowEvent(TemporalEvent):
    event_name = TemporalEvents.SIGNAL_WORKFLOW.value

    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        # Schema functions are selected dynamically and are untyped.
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]
            self.event_name, provider="temporal", direction=SpanDirection.OUTBOUND
        )


@dataclass
class TemporalQueryWorkflowEvent(TemporalEvent):
    event_name = TemporalEvents.QUERY_WORKFLOW.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        # Schema functions are selected dynamically and are untyped.
        self.operation_name = schematize_url_operation(  # type: ignore[operator]
            self.event_name, protocol="temporal", direction=SpanDirection.OUTBOUND
        )


@dataclass
class TemporalRunActivityEvent(TemporalEvent):
    event_name = TemporalEvents.RUN_ACTIVITY.value

    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        # Schema functions are selected dynamically and are untyped.
        self.operation_name = schematize_messaging_operation(  # type: ignore[operator]
            self.event_name, provider="temporal", direction=SpanDirection.PROCESSING
        )
