from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry.context import Context as OtelContext
from opentelemetry.trace import SpanKind as OtelSpanKind
from opentelemetry.trace import Tracer as OtelTracer
from opentelemetry.trace import TracerProvider as OtelTracerProvider
from opentelemetry.trace import use_span
from opentelemetry.trace.propagation import get_current_span
from opentelemetry.trace.span import INVALID_SPAN
from opentelemetry.trace.span import Span as OtelSpan

import ddtrace
from ddtrace.internal.logger import get_logger
from ddtrace.opentelemetry import _context
from ddtrace.opentelemetry.span import Span


if TYPE_CHECKING:
    from typing import Dict
    from typing import Iterator
    from typing import Optional
    from typing import Sequence
    from typing import Union

    from opentelemetry.trace import Link as OtelLink
    from opentelemetry.util.types import AttributeValue as OtelAttributeValue


log = get_logger(__name__)


class TracerProvider(OtelTracerProvider):
    """
    Returns a Tracer, creating one if one with the given name and version is not already created.
    One TracerProvider should be initialized and set per application.
    """

    def __init__(self) -> None:
        self._ddtracer = ddtrace.tracer
        # The Opentelemetry API allows only one implementation of the otel sdk. We should avoid overriding the
        # default otel context management unless the ddtrace TracerProvider is initialized and (hopefully) set.
        # TODO: call _context.wrap_otel_context() when opentelemetry.trace.set_tracer_provider(...) is called.
        _context.wrap_otel_context()
        super().__init__()

    def get_tracer(
        self,
        instrumenting_module_name,  # type: str
        instrumenting_library_version=None,  # type: Optional[str]
        schema_url=None,  # type: Optional[str]
    ):
        # type: (...) -> OtelTracer
        """Returns an opentelmetry compatible `Tracer`."""
        # TODO: Do we need to do anything else with the other arguments?
        return Tracer(self._ddtracer)


class Tracer(OtelTracer):
    """This Tracer implements the opentelemetry-api to generate datadog traces."""

    def __init__(self, datadog_tracer):
        # (ddtrace.Tracer) -> None
        self._tracer = datadog_tracer
        super(Tracer, self).__init__()

    def start_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[OtelContext]
        kind=OtelSpanKind.INTERNAL,  # type: OtelSpanKind
        attributes=None,  # type: Optional[Dict[str, OtelAttributeValue]]
        links=None,  # type: Optional[Sequence[OtelLink]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
    ):
        # type: (...) -> OtelSpan
        """Creates and starts an opentelemetry span"""
        # Get active otel span
        curr_otel_span = get_current_span(context)
        # Get either a datadog span or datadog context object from otel span
        if curr_otel_span is INVALID_SPAN:
            dd_active = None  # type: Optional[Union[ddtrace.context.Context, ddtrace.Span]]
        elif isinstance(curr_otel_span, Span):
            dd_active = curr_otel_span._ddspan
        elif isinstance(curr_otel_span, OtelSpan):
            trace_id, span_id, *_ = curr_otel_span.get_span_context()
            dd_active = ddtrace.context.Context(trace_id, span_id)
        else:
            raise ValueError("Active Span is not supported by ddtrace: %s" % curr_otel_span)
        # Create a new Datadog span (not activated), then return a valid OTel span
        dd_span = self._tracer.start_span(name, child_of=dd_active, activate=False)
        return Span(
            dd_span,
            kind=kind,
            attributes=attributes,
            start_time=start_time,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        )

    @contextmanager
    def start_as_current_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[OtelContext]
        kind=OtelSpanKind.INTERNAL,  # type: OtelSpanKind
        attributes=None,  # type: Optional[Dict[str, OtelAttributeValue]]
        links=None,  # type: Optional[Sequence[OtelLink]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
        end_on_exit=True,  # type: bool
    ):
        # type: (...) -> Iterator[Span]
        """Context manager for creating and activating a new opentelemetry span"""
        # Create a new non-active OTel span wrapper
        span = self.start_span(
            name,
            context=context,
            kind=kind,
            attributes=attributes,
            links=links,
            start_time=start_time,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        )

        with use_span(
            span,
            end_on_exit=end_on_exit,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        ) as span:
            yield span
