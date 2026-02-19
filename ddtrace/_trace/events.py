from dataclasses import dataclass
from dataclasses import field
from typing import TYPE_CHECKING
from typing import ClassVar
from typing import Optional

from ddtrace.internal.core.events import Event


if TYPE_CHECKING:
    from ddtrace._trace.provider import ActiveTrace


@dataclass
class TracingEvent(Event):
    """TracingEvent is a specialization of Event. It enforces minimal tracing attributes
    on any TracingEvent. Its purpose is to be used with core.context_with_event
    """

    span_type: ClassVar[str]
    span_kind: ClassVar[str]

    # These attributes are required but can be known only at instance-creation time.
    span_name: str = field(init=False)
    component: str = field()

    tags: dict[str, str] = field(default_factory=dict, init=False)
    # if False, handlers should not finish a span when the Context finishes.
    _end_span: bool = field(default=True, init=False)

    # Optional tracing related attibutes
    activate: bool = False  # if True, activate the span as active span context
    use_active_context: bool = True  # if True, use the active span ctx as parent
    service: Optional[str] = None
    distributed_context: Optional["ActiveTrace"] = None  # if set, use the context as parent
    resource: Optional[str] = None
    measured: bool = False
