from typing import TYPE_CHECKING

from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanContext
from opentelemetry.trace import SpanKind
from opentelemetry.trace import Status
from opentelemetry.trace import StatusCode

from ddtrace.constants import SPAN_KIND


if TYPE_CHECKING:
    from typing import Dict
    from typing import Optional
    from typing import Union

    from opentelemetry.util.types import AttributeValue
    from opentelemetry.util.types import Attributes

    from ddtrace.span import Span as DDSpan


class Span(OtelSpan):
    """Implements the Opentelemetry Span API to create and configure datadog spans"""

    def __init__(
        self,
        datadog_span,  # type: DDSpan
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: Optional[Dict[str, AttributeValue]]
        start_time=None,  # type: Optional[int]
        record_exception=None,  # type: Optional[bool]
        set_status_on_exception=None,  # type: Optional[bool]
    ):
        # (...) -> None
        if start_time is not None:
            # otel instrumentation tracks time in nanoseconds while ddtrace uses seconds
            datadog_span.start = start_time / 1e9

        self._ddspan = datadog_span

        # BUG: self.kind is required by the otel flask instrumentation, this property is NOT defined in the otel-api.
        # TODO: Propose a fix in opentelemetry-python-contrib
        self.kind = kind
        if kind != SpanKind.INTERNAL:
            # Only set if it isn't "internal" to save on bytes
            self.set_attribute(SPAN_KIND, kind.name.lower())

        if attributes:
            self.set_attributes(attributes)

        # BUG: record_exception and set_status_on_exception are not used in Span.__exit__()
        # TODO: add record_exception and set_status_on_exception attributes to ddspan

    def end(self, end_time=None):
        # type: (Optional[int]) -> None
        """Ends a datadog span"""
        self._ddspan.finish(finish_time=end_time)

    def get_span_context(self):
        # type: () -> SpanContext
        """Returns an opentelemetry SpanContext using values from the active datadog span"""
        # TODO: This is the bare minimum to get current context, but what about tracestate and flags?
        return SpanContext(self._ddspan.trace_id, self._ddspan.span_id, False)

    def set_attributes(self, attributes):
        # type: (Dict[str, AttributeValue]) -> None
        """Sets attributes/tags"""
        for k, v in attributes.items():
            self.set_attribute(k, v)

    def set_attribute(self, key, value):
        # type: (str, AttributeValue) -> None
        """Sets an attribute (aka tags) on the internal datadog span"""
        if not self.is_recording():
            return
        self._ddspan.set_tag(key, value)

    def add_event(self, name, attributes=None, timestamp=None):
        # type: (str, Optional[Attributes], Optional[int]) -> None
        """NOOP - events are not supported in the datadog span API"""
        return

    def update_name(self, name):
        # type: (str) -> None
        """Updates the operation name on the internal datadog span."""
        if not self.is_recording():
            return
        self._ddspan.name = name

    def is_recording(self):
        # type: () -> bool
        """Returns True if attributes can be set on a span"""
        return not self._ddspan.finished

    def set_status(self, status, description=None):
        # type: (Union[Status, StatusCode], Optional[str]) -> None
        """Since datadog spans have the default status OK and Datadog spans do not support StatusCode.UNSET.
        This method only support supports converting the status type from OK to ERROR.
        """
        if not self.is_recording():
            return
        elif isinstance(status, Status):
            status_code = status.status_code
        else:
            status_code = status

        if status_code is StatusCode.ERROR:
            self._ddspan.error = 1

    def record_exception(self, exception, attributes=None, timestamp=None, escaped=False):
        # type: (BaseException, Optional[Attributes], Optional[int], bool) -> None
        """Records an exception on datadog span."""
        if not self.is_recording():
            return
        self._ddspan._set_exc_tags(type(exception), exception, exception.__traceback__)
        if attributes:
            self.set_attributes(attributes)

    def __enter__(self):
        # type: () -> Span
        """Invoked when `Span` is used as a context manager.
        Returns the `Span` itself.
        """
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Ends context manager on datadog span"""
        if exc_val:
            self.record_exception(exc_val)
            self.set_status(StatusCode.ERROR)
        self.end()
