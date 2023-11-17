from enum import Enum
from typing import TYPE_CHECKING

from opentelemetry.trace import Span as OtelSpan
from opentelemetry.trace import SpanContext
from opentelemetry.trace import SpanKind
from opentelemetry.trace import Status
from opentelemetry.trace import StatusCode
from opentelemetry.trace.span import TraceFlags
from opentelemetry.trace.span import TraceState

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import SPAN_KIND
from ddtrace.internal import core
from ddtrace.internal.compat import time_ns
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:
    from typing import Callable
    from typing import Mapping
    from typing import Optional
    from typing import Union

    from opentelemetry.util.types import Attributes
    from opentelemetry.util.types import AttributeValue

    from ddtrace.internal.compat import NumericType
    from ddtrace.span import Span as DDSpan


log = get_logger(__name__)


def _ddmap(span, attribute, value):
    # type: (DDSpan, str, Union[bytes, NumericType]) -> DDSpan
    if attribute.startswith("meta") or attribute.startswith("metrics"):
        meta_key = attribute.split("'")[1] if len(attribute.split("'")) == 3 else None
        if meta_key:
            span.set_tag(meta_key, value)
    else:
        setattr(span, attribute, value)
    return span


_OTelDatadogMapping = {
    "service.name": "service",
    "resource.name": "resource",
    "span.type": "span_type",
    "analytics.event": "metrics['_dd1.sr.eausr']",
}


class Span(OtelSpan):
    """Initializes an OpenTelemetry compatible shim for a Datadog span

    TODO: Add mapping table from otel to datadog
    """

    _RECORD_EXCEPTION_KEY = "_dd.otel.record_exception"
    _SET_EXCEPTION_STATUS_KEY = "_dd.otel.set_status_on_exception"

    def __init__(
        self,
        datadog_span,  # type: DDSpan
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: Optional[Mapping[str, AttributeValue]]
        start_time=None,  # type: Optional[int]
        record_exception=None,  # type: Optional[bool]
        set_status_on_exception=None,  # type: Optional[bool]
    ):
        # type: (...) -> None
        if start_time is not None:
            # start_time should be set in nanoseconds
            datadog_span.start_ns = start_time

        self._ddspan = datadog_span
        if record_exception is not None:
            self._record_exception = record_exception
        if set_status_on_exception is not None:
            self._set_status_on_exception = set_status_on_exception

        if kind is not SpanKind.INTERNAL:
            # Only set if it isn't "internal" to save on bytes
            self.set_attribute(SPAN_KIND, kind.name.lower())

        if attributes:
            self.set_attributes(attributes)

    @property
    def _record_exception(self):
        # type: () -> bool
        # default value is True, if record exception key is not set return True
        return core.get_item(self._RECORD_EXCEPTION_KEY, span=self._ddspan) is not False

    @_record_exception.setter
    def _record_exception(self, value):
        # type: (bool) -> None
        core.set_item(self._RECORD_EXCEPTION_KEY, value, span=self._ddspan)

    @property
    def _set_status_on_exception(self):
        # type: () -> bool
        # default value is True, if set status on exception key is not set return True
        return core.get_item(self._SET_EXCEPTION_STATUS_KEY, span=self._ddspan) is not False

    @_set_status_on_exception.setter
    def _set_status_on_exception(self, value):
        # type: (bool) -> None
        core.set_item(self._SET_EXCEPTION_STATUS_KEY, value, span=self._ddspan)

    def end(self, end_time=None):
        # type: (Optional[int]) -> None
        """
        Marks the end time of a span. This method should be called once.

        :param end_time: The end time of the span, in nanoseconds. Defaults to ``now``.
        """
        if end_time is None:
            end_time = time_ns()
        override_name = self._datadog_operation_name
        if override_name:
            self._ddspan.name = override_name
        self._ddspan._finish_ns(end_time)

    @property
    def kind(self):
        """Gets span kind attribute"""
        # BUG: Span.kind is required by the otel library instrumentation (ex: flask, asgi, django) but
        # this property is only defined in the opentelemetry-sdk and NOT defined the opentelemetry-api.
        # TODO: Propose a fix in opentelemetry-python-contrib project
        return self._ddspan._meta.get(SPAN_KIND, SpanKind.INTERNAL.name.lower())

    def get_span_context(self):
        # type: () -> SpanContext
        """Returns an OpenTelemetry SpanContext"""
        ts = None
        tf = TraceFlags.DEFAULT
        if self._ddspan.context:
            ts = TraceState.from_header([self._ddspan.context._tracestate])
            if self._ddspan.context.sampling_priority and self._ddspan.context.sampling_priority > 0:
                tf = TraceFlags.SAMPLED

        return SpanContext(self._ddspan.trace_id, self._ddspan.span_id, False, tf, ts)

    def set_attributes(self, attributes):
        # type: (Mapping[str, AttributeValue]) -> None
        """Sets attributes/tags"""
        for k, v in attributes.items():
            self.set_attribute(k, v)

    def set_attribute(self, key, value):
        # type: (str, AttributeValue) -> None
        """Sets an attribute or service name on a tag"""
        if not self.is_recording():
            return

        # Override reserved OTel span attributes
        ddattribute = _OTelDatadogMapping.get(key)
        if ddattribute is not None:
            _ddmap(self._ddspan, ddattribute, value)
            return

        self._ddspan.set_tag(key, value)

    def add_event(self, name, attributes=None, timestamp=None):
        # type: (str, Optional[Attributes], Optional[int]) -> None
        """NOOP - events are not yet supported"""
        return

    def update_name(self, name):
        # type: (str) -> None
        """Updates the name of a span"""
        if not self.is_recording():
            return
        self._ddspan.resource = name

    def is_recording(self):
        # type: () -> bool
        """Returns False if Span.end() is called."""
        return not self._ddspan.finished

    def set_status(self, status, description=None):
        # type: (Union[Status, StatusCode], Optional[str]) -> None
        """
        Updates a Span from StatusCode.OK to StatusCode.ERROR.
        Note - The default status is OK. Setting the status to StatusCode.UNSET or updating the
        status from StatusCode.ERROR to StatusCode.OK is not supported.
        """
        if not self.is_recording():
            return

        if isinstance(status, Status):
            status_code = status.status_code
            message = status.description
            log.warning("Description %s ignored. Use either `Status` or `(StatusCode, Description)`", description)
        else:
            status_code = status
            message = description

        if status_code is StatusCode.ERROR:
            self._ddspan.error = 1
            if message:
                self.set_attribute(ERROR_MSG, message)

    def record_exception(self, exception, attributes=None, timestamp=None, escaped=False):
        # type: (BaseException, Optional[Attributes], Optional[int], bool) -> None
        """
        Records the type, message, and traceback of an exception as Span attributes.
        Note - Span Events are not yet supported.
        """
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
        """Ends Span context manager"""
        if exc_val:
            if self._record_exception:
                self.record_exception(exc_val)
            if self._set_status_on_exception:
                # do not overwrite the status message set by record exception
                self.set_status(StatusCode.ERROR)
        self.end()

    @property
    def _datadog_operation_name(self):
        # Adapted from https://github.com/DataDog/dd-trace-java/blob/4131e509a94db430b47104769800ec14de5f0a0d/dd-java-agent/instrumentation/opentelemetry/opentelemetry-1.4/src/main/java/datadog/trace/instrumentation/opentelemetry14/trace/OtelConventions.java#L107
        ddspan = self._ddspan
        span_kind = self.kind

        operation_name = ddspan.get_tag("operation.name")
        if operation_name:
            return operation_name

        if ddspan.get_tag("http.request.method"):
            if span_kind == SpanKind.SERVER:
                return "http.server.request"
            if span_kind == SpanKind.CLIENT:
                return "http.client.request"

        db_system = ddspan.get_tag("db.system")
        if db_system and span_kind == SpanKind.CLIENT:
            return f"{db_system}.query"

        messaging_system = ddspan.get_tag("messaging.system")
        messaging_operation = ddspan.get_tag("messaging.operation")
        if (
            messaging_system
            and messaging_operation
            and (
                span_kind == SpanKind.CONSUMER
                or span_kind == SpanKind.PRODUCER
                or span_kind == SpanKind.CLIENT
                or span_kind == SpanKind.SERVER
            )
        ):
            return messaging_system + "." + messaging_operation

        rpc_system = ddspan.get_tag("rpc.system")
        if span_kind == SpanKind.CLIENT and rpc_system == "aws-api":
            rpc_service = ddspan.get_tag("rpc.service")
            if not rpc_service:
                return "aws.client.request"
            return f"aws.{rpc_service}.request"
        if span_kind == SpanKind.CLIENT and rpc_system:
            return f"{rpc_system}.client.request"
        if span_kind == SpanKind.SERVER and rpc_system:
            return f"{rpc_system}.server.request"

        faas_invoked_provider = ddspan.get_tag("faas.invoked_provider")
        faas_invoked_name = ddspan.get_tag("faas.invoked_name")
        if span_kind == SpanKind.CLIENT and faas_invoked_provider and faas_invoked_name:
            return f"{faas_invoked_provider}.{faas_invoked_name}.invoke"
        faas_trigger = ddspan.get_tag("faas.trigger")
        if span_kind == SpanKind.SERVER and faas_trigger:
            return f"{faas_trigger}.invoke"

        graphql_operation_type = ddspan.get_tag("graphql.operation.type")
        if graphql_operation_type:
            return "graphql.server.request"

        network_protocol_name = ddspan.get_tag("network.protocol.name")
        if span_kind == SpanKind.SERVER:
            if network_protocol_name:
                return f"{network_protocol_name}.server.request"
            else:
                return "server.request"
        if span_kind == SpanKind.CLIENT:
            if network_protocol_name:
                return f"{network_protocol_name}.client.request"
            else:
                return "server.request"

        return span_kind
