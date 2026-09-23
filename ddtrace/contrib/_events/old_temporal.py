from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


Carrier = dict[str, str]


class TemporalEvents(Enum):
    ACTIVATE_WORKFLOW = "temporal.workflow.activate"
    OPERATION = "temporal.operation"
    PROPAGATE = "temporal.propagate"
    WORKFLOW_LOG = "temporal.workflow.log"


class TemporalOperationNames(str, Enum):
    CREATE_SCHEDULE = "CreateSchedule"
    HANDLE_QUERY = "HandleQuery"
    HANDLE_SIGNAL = "HandleSignal"
    HANDLE_UPDATE = "HandleUpdate"
    QUERY_WORKFLOW = "QueryWorkflow"
    RUN_ACTIVITY = "RunActivity"
    RUN_WORKFLOW = "RunWorkflow"
    SIGNAL_CHILD_WORKFLOW = "SignalChildWorkflow"
    SIGNAL_EXTERNAL_WORKFLOW = "SignalExternalWorkflow"
    SIGNAL_WITH_START_WORKFLOW = "SignalWithStartWorkflow"
    SIGNAL_WORKFLOW = "SignalWorkflow"
    START_ACTIVITY = "StartActivity"
    START_CHILD_WORKFLOW = "StartChildWorkflow"
    START_NEXUS_OPERATION = "StartNexusOperation"
    RUN_NEXUS_OPERATION_START_HANDLER = "RunStartNexusOperationHandler"
    RUN_NEXUS_OPERATION_CANCEL_HANDLER = "RunCancelNexusOperationHandler"
    UPDATE_WITH_START_WORKFLOW = "UpdateWithStartWorkflow"
    UPDATE_WORKFLOW = "UpdateWorkflow"
    START_WORKFLOW = "StartWorkflow"
    VALIDATE_UPDATE = "ValidateUpdate"


@dataclass(frozen=True)
class FinishContext:
    """Context passed to a user-supplied ``on_span_finish`` callback."""

    operation: str
    exception: BaseException | None


@dataclass(frozen=True)
class FinishResult:
    """Returned by ``on_span_finish`` to add tags before a span is finished."""

    extra_tags: Mapping[str, Any] | None = None


@dataclass
class TemporalOperationEvent(TracingEvent):
    event_name = TemporalEvents.OPERATION.value
    span_kind = SpanKind.INTERNAL
    span_type = ""

    operation: str = event_field()
    attributes: Mapping[str, Any] = event_field(default_factory=dict)
    incoming_carrier: Mapping[str, str] | None = event_field(default=None)
    workflow_carrier: Mapping[str, str] | None = event_field(default=None)
    workflow_span_id: int | None = event_field(default=None)
    idempotency_key: str | None = event_field(default=None)
    deterministic_root_trace: bool = event_field(default=False)
    start_ns: int | None = event_field(default=None)
    inject: bool = event_field(default=False)
    parent_from_header: bool = event_field(default=False)
    allow_invalid_parent_spans: bool = event_field(default=False)
    extra_tags: Mapping[str, str] = event_field(default_factory=dict)
    on_span_finish: Callable[[FinishContext], FinishResult | None] | None = event_field(default=None)
    ignored_exceptions: tuple[type[BaseException], ...] = event_field(default_factory=tuple)
    continued_as_new_exception: type[BaseException] | None = event_field(default=None)
    outgoing_carrier: Carrier = event_field(default_factory=dict)

    def __post_init__(self) -> None:
        self.operation_name = f"temporal.{self.operation}"


@dataclass
class TemporalPropagationEvent(Event):
    event_name = TemporalEvents.PROPAGATE.value

    incoming_carrier: Mapping[str, str] | None = event_field(default=None)
    workflow_span_id: int | None = event_field(default=None)
    allow_invalid_parent_spans: bool = event_field(default=False)
    use_active_context: bool = event_field(default=False)
    outgoing_carrier: Carrier = event_field(default_factory=dict)


@dataclass
class TemporalWorkflowLogEvent(Event):
    event_name = TemporalEvents.WORKFLOW_LOG.value

    record: logging.LogRecord = event_field()


@dataclass
class TemporalActivateWorkflowEvent(Event):
    event_name = TemporalEvents.ACTIVATE_WORKFLOW.value

    operation_context: Any = event_field()
