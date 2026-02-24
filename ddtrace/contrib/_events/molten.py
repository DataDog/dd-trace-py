from dataclasses import InitVar
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


class MoltenEvents(Enum):
    MOLTEN_TRACE_FUNCTION = "molten.trace.func"
    MOLTEN_ROUTER_MATCH = "molten.router.match"


@dataclass
class MoltenTraceEvent(TracingEvent):
    event_name = MoltenEvents.MOLTEN_TRACE_FUNCTION.value

    span_kind = SpanKind.SERVER
    span_type = SpanTypes.WEB

    function_name: InitVar[str] = event_field()

    def __post_init__(self, function_name):
        self.span_name = function_name


@dataclass
class MoltenRouterMatchEvent(Event):
    event_name = MoltenEvents.MOLTEN_ROUTER_MATCH.value

    route: Any
