from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry.context import Context as OtelContext
from opentelemetry.trace import SpanKind as OtelSpanKind
from opentelemetry.trace import Tracer as OtelTracer
from opentelemetry.trace import TracerProvider as OtelTracerProvider

from ddtrace import tracer as ddtracer
from ddtrace.internal.logger import get_logger
from ddtrace.opentelemetry.span import Span


if TYPE_CHECKING:
    from typing import Dict
    from typing import Iterator
    from typing import Optional
    from typing import Sequence

    from opentelemetry.trace import Link as OtelLink
    from opentelemetry.trace import Span as OtelSpan
    from opentelemetry.util.types import AttributeValue as OtelAttributeValue


log = get_logger(__name__)


class TracerProvider(OtelTracerProvider):
    def get_tracer(
        self,
        instrumenting_module_name,  # type: str
        instrumenting_library_version=None,  # type: Optional[str]
        schema_url=None,  # type: Optional[str]
    ):
        # type: (...) -> OtelTracer
        """Returns an opentelmetry compatible `Tracer`."""
        return Tracer(ddtracer)


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
        if record_exception is False or set_status_on_exception is False:
            log.warning(
                """
                Calling Tracer.start_span with record_exception=False or set_status_on_exception=False is not supported.
                These parameters will be ignored. To ignore exceptions on spans use:
                Tracer.start_as_current_span(..., record_exception=False, set_status_on_exception=False) or
                opentelemtry.trace.use_span(..., record_exception=False, set_status_on_exception=False)
                """
            )
            record_exception = True
            set_status_on_exception = True

        # TODO: use opentelemetry.trace.get_current_span(context) instead
        dd_context = self._tracer.context_provider.active()
        # Create a new Datadog span (not activated), then return an OTel span wrapper
        dd_span = self._tracer.start_span(name, child_of=dd_context, activate=False)
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
            record_exception=True,
            set_status_on_exception=True,
        )

        # Activate the span in the Datadog context manager
        # TODO: use opentelemetry.set_span_in_context()
        self._tracer.context_provider.activate(span._ddspan)
        yield span

        if end_on_exit:
            span.end()
