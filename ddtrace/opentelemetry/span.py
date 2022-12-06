from typing import TYPE_CHECKING

from opentelemetry.trace import Span as Span
from opentelemetry.trace import SpanContext
from opentelemetry.trace import SpanKind
from opentelemetry.trace import Status
from opentelemetry.trace import StatusCode
from opentelemetry.trace.span import DEFAULT_TRACE_OPTIONS
from opentelemetry.trace.span import DEFAULT_TRACE_STATE


if TYPE_CHECKING:
    from typing import Dict
    from typing import Optional
    from typing import Union

    from opentelemetry.util.types import AttributeValue
    from opentelemetry.util.types import Attributes

    from ddtrace.span import Span as DDSpan


class OtelSpan(Span):
    def __init__(
        self,
        datadog_span,  # type: DDSpan
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: Optional[Attributes]
        start_time=None,  # type: Optional[int]
    ):
        # (...) -> None
        if start_time is not None:
            datadog_span.start = start_time

        # Only set if it isn't "internal" to save on bytes
        if kind != SpanKind.INTERNAL:
            # TODO: Validate/clean this?
            datadog_span.set_tag_str("span.kind", kind.name.lower())

        # TODO: Are there other default tags we want to set?

        self._span = datadog_span

        if attributes:
            self.set_attributes(attributes)

    def end(self, end_time=None):
        # type: (Optional[int]) -> None
        """Sets the current time as the span's end time.
        The span's end time is the wall time at which the operation finished.
        Only the first call to `end` should modify the span, and
        implementations are free to ignore or raise on further calls.
        """
        self._span.finish(finish_time=end_time)

    def get_span_context(self):
        # type: () -> SpanContext
        """Gets the span's SpanContext.
        Get an immutable, serializable identifier for this span that can be
        used to create new child spans.
        Returns:
            A :class:`opentelemetry.trace.SpanContext` with a copy of this span's immutable state.
        """
        # TODO: This is the bare minimum to get current context, but what about tracestate and flags?
        return SpanContext(self._span.trace_id, self._span.span_id, False, DEFAULT_TRACE_OPTIONS, DEFAULT_TRACE_STATE)

    def set_attributes(self, attributes):
        # type: (Dict[str, AttributeValue]) -> None
        """Sets Attributes.
        Sets Attributes with the key and value passed as arguments dict.
        Note: The behavior of `None` value attributes is undefined, and hence
        strongly discouraged. It is also preferred to set attributes at span
        creation, instead of calling this method later since samplers can only
        consider information already present during span creation.
        """
        for k, v in attributes.items():
            self.set_attribute(k, v)

    def set_attribute(self, key, value):
        # type: (str, AttributeValue) -> None
        """Sets an Attribute.
        Sets a single Attribute with the key and value passed as arguments.
        Note: The behavior of `None` value attributes is undefined, and hence
        strongly discouraged. It is also preferred to set attributes at span
        creation, instead of calling this method later since samplers can only
        consider information already present during span creation.
        """
        if isinstance(value, float) or isinstance(value, int):
            self._span.set_metric(key, value)
        else:
            self._span.set_tag_str(key, str(value))

    def add_event(self, name, attributes=None, timestamp=None):
        # type: (str, Attributes, Optional[int]) -> None
        """Adds an `Event`.
        Adds a single `Event` with the name and, optionally, a timestamp and
        attributes passed as arguments. Implementations should generate a
        timestamp if the `timestamp` argument is omitted.
        """
        return

    def update_name(self, name):
        # type: (str) -> None
        """Updates the `Span` name.
        This will override the name provided via :func:`opentelemetry.trace.Tracer.start_span`.
        Upon this update, any sampling behavior based on Span name will depend
        on the implementation.
        """
        self._span.name = name

    def is_recording(self) -> bool:
        """Returns whether this span will be recorded.
        Returns true if this Span is active and recording information like
        events with the add_event operation and attributes using set_attribute.
        """
        return not self._span.finished

    def set_status(self, status, description=None):
        # type: (Union[Status, StatusCode], Optional[str]) -> None
        """Sets the Status of the Span. If used, this will override the default
        Span status.
        """
        return

    def record_exception(self, exception, attributes=None, timestamp=None, escaped=False):
        # type: (Exception, Attributes, Optional[int], bool) -> None
        """Records an exception as a span event."""
        return
