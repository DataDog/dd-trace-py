from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import TYPE_CHECKING
from typing import ClassVar
from typing import Mapping
from typing import Optional

from ddtrace.internal.core.events import Event
from ddtrace.internal.settings.integration import IntegrationConfig


if TYPE_CHECKING:
    # DEV: potential source of circular import in the future
    from ddtrace._trace.provider import ActiveTrace


class TracingEvents(Enum):
    SPAN_LIFECYCLE = "span.lifecycle"


@dataclass
class TracingEvent(Event):
    """TracingEvent is a specialization of Event. It enforces minimal tracing attributes
    on any TracingEvent. Its purpose is to be used with core.context_with_event.
    """

    event_name = TracingEvents.SPAN_LIFECYCLE.value

    span_type: ClassVar[str]
    span_kind: ClassVar[str]
    # When True, ExecutionContext also dispatches context.started.<event_name>.<component>
    # so integrations can subscribe to scoped events without guards or subclasses.
    _emit_scoped_event: ClassVar[bool] = False

    # These attributes are required but can be known only at instance-creation time.
    component: str = field()
    integration_config: IntegrationConfig = field()

    # operation_name must be provided at __post_init__.
    # This allows to deal with constant and non constant operation name
    # while still enforcing the value
    operation_name: str = field(init=False)

    tags: dict[str, str] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    # if False, handlers should not finish a span when the Context finishes.
    _end_span: bool = field(default=True, init=False)

    # Optional tracing related attributes
    activate: bool = True  # if False, does not activate the span as active span context
    use_active_context: bool = True  # if True, use the active span ctx as parent
    service: Optional[str] = None
    distributed_context: Optional["ActiveTrace"] = None  # if set, use the context as parent
    resource: Optional[str] = None
    measured: bool = True
    activate_distributed_headers: bool = False

    @classmethod
    def create(
        cls,
        component: str,
        integration_config: "IntegrationConfig",
        operation_name: str,
        span_type: str,
        span_kind: str,
        tags: Optional[Mapping[str, str]] = None,
        activate: bool = True,
        use_active_context: bool = True,
        service: Optional[str] = None,
        distributed_context: Optional["ActiveTrace"] = None,
        resource: Optional[str] = None,
        measured: bool = True,
        activate_distributed_headers: bool = False,
    ) -> "TracingEvent":
        """This methods allow to dispatch a TracingEvent to create/finish a span
        without having to define a subclass when the span has no additional properties.

        This should be used as a replacement to the for context_name in (): _start_span/_finish_span
        in trace_handlers.py
        """

        event = cls(
            component=component,
            integration_config=integration_config,
            tags=dict(tags or {}),
            activate=activate,
            use_active_context=use_active_context,
            service=service,
            distributed_context=distributed_context,
            resource=resource,
            measured=measured,
            activate_distributed_headers=activate_distributed_headers,
        )
        event.operation_name = operation_name
        setattr(event, "span_type", span_type)
        setattr(event, "span_kind", span_kind)
        return event
