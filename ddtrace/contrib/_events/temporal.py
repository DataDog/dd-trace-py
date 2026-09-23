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
    WORKFLOW = "temporal.workflow"
    ACTIVITY = "temporal.activity"
    ACTIVATE_WORKFLOW = "temporal.workflow.activate"
    OPERATION = "temporal.operation"
    PROPAGATE = "temporal.propagate"
    WORKFLOW_LOG = "temporal.workflow.log"


@dataclass
class TemporalOperationEvent(TracingEvent):
    event_name = TemporalEvents.OPERATION.value

@dataclass
class TemporalWorkflowEvent(TracingEvent):
    event_name = TemporalEvents.WORKFLOW.value

    operation: str = event_field()
    input: Any = event_field()

@dataclass
class TemporalActivityEvent(TracingEvent):
    event_name = TemporalEvents.ACTIVITY.value

