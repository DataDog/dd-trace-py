from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import ClassVar
from typing import Optional

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


if TYPE_CHECKING:
    # DEV: potential source of circular import in the future
    from ddtrace._trace.provider import ActiveTrace
    from ddtrace.internal.settings.integration import IntegrationConfig


@dataclass
class TracingEvent(Event):
    """TracingEvent is a specialization of Event. It enforces minimal tracing attributes
    on any TracingEvent. Its purpose is to be used with core.context_with_event.
    """

    span_type: ClassVar[str]
    span_kind: ClassVar[str] = "internal"

    # AIDEV-NOTE: component and config use event_field() instead of field() for Python 3.9
    # compatibility. Subclasses may override span_name with event_field() (which adds
    # default=None on 3.9), and since span_name is positioned before component/config,
    # plain field() would cause "non-default argument follows default argument" errors.
    span_name: str = field(init=False)
    component: str = event_field()
    config: "IntegrationConfig" = event_field()

    tags: dict[str, str] = field(default_factory=dict)
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
