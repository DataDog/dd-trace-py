from dataclasses import dataclass
from enum import Enum

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes


class TemporalEvents(Enum):
    START_WORKFLOW = "temporal.start_workflow"
    SIGNAL_WORKFLOW = "temporal.signal_workflow"
    QUERY_WORKFLOW = "temporal.query_workflow"
    RUN_ACTIVITY = "temporal.run_activity"


@dataclass
class TemporalStartWorkflowEvent(TracingEvent):
    event_name = TemporalEvents.START_WORKFLOW.value

    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        self.operation_name = self.event_name


@dataclass
class TemporalSignalWorkflowEvent(TracingEvent):
    event_name = TemporalEvents.SIGNAL_WORKFLOW.value

    span_kind = SpanKind.PRODUCER
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        self.operation_name = self.event_name


@dataclass
class TemporalQueryWorkflowEvent(TracingEvent):
    event_name = TemporalEvents.QUERY_WORKFLOW.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        self.operation_name = self.event_name


@dataclass
class TemporalRunActivityEvent(TracingEvent):
    event_name = TemporalEvents.RUN_ACTIVITY.value

    span_kind = SpanKind.CONSUMER
    span_type = SpanTypes.WORKER

    def __post_init__(self) -> None:
        self.operation_name = self.event_name
