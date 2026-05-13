import abc
from collections.abc import Iterator
from collections.abc import Mapping
from collections.abc import Sequence
from contextlib import contextmanager
from time import time_ns
import threading
from typing import TYPE_CHECKING
from typing import Optional

from opentelemetry import version
from opentelemetry.context import Context as OtelContext  # noqa:F401
from opentelemetry.trace import SpanKind as OtelSpanKind
from opentelemetry.trace import Tracer as OtelTracer
from opentelemetry.trace import TracerProvider as OtelTracerProvider
from opentelemetry.trace import use_span
from opentelemetry.trace.propagation import get_current_span
from opentelemetry.trace.span import DEFAULT_TRACE_OPTIONS
from opentelemetry.trace.span import INVALID_SPAN

from ddtrace._trace.provider import ActiveTrace as DDActiveTrace
from ddtrace.internal.constants import SPAN_API_OTEL
from ddtrace.internal.logger import get_logger
from ddtrace.propagation.http import _TraceContext
from ddtrace.trace import tracer as ddtracer

from .span import Span


if TYPE_CHECKING:
    from opentelemetry.trace import Link as OtelLink  # noqa:F401
    from opentelemetry.trace.span import Span as OtelSpan  # noqa:F401
    from opentelemetry.util.types import AttributeValue as OtelAttributeValue  # noqa:F401

    from ddtrace.context import Context as DDContext  # noqa:F401


log = get_logger(__name__)


class SpanProcessor(metaclass=abc.ABCMeta):
    """Interface for span processors that hook into OTel span lifecycle events.

    Mirrors the ``opentelemetry.sdk.trace.SpanProcessor`` interface so that
    processors written for the OTel SDK can be used with the ddtrace
    ``TracerProvider`` without modification.
    """

    def on_start(self, span: "OtelSpan", parent_context: Optional[OtelContext] = None) -> None:
        """Called when a span is started."""

    def on_end(self, span: "OtelSpan") -> None:
        """Called when a span is ended. The span is no longer recording at this point."""

    def shutdown(self) -> None:
        """Called when the processor is shut down."""

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flushes any pending spans. Returns True if successful within the timeout."""
        return True


class SynchronousMultiSpanProcessor:
    """Forwards span events to a collection of :class:`SpanProcessor` instances sequentially.

    Mirrors ``opentelemetry.sdk.trace.SynchronousMultiSpanProcessor``.
    """

    def __init__(self) -> None:
        self._span_processors: tuple = ()
        self._lock = threading.Lock()

    def add_span_processor(self, span_processor: SpanProcessor) -> None:
        """Appends a span processor to the chain."""
        with self._lock:
            self._span_processors = self._span_processors + (span_processor,)

    def on_start(self, span: "OtelSpan", parent_context: Optional[OtelContext] = None) -> None:
        for sp in self._span_processors:
            sp.on_start(span, parent_context=parent_context)

    def on_end(self, span: "OtelSpan") -> None:
        for sp in self._span_processors:
            sp.on_end(span)

    def shutdown(self) -> None:
        for sp in self._span_processors:
            sp.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        deadline_ns = time_ns() + timeout_millis * 1_000_000
        for sp in self._span_processors:
            remaining_ms = max(0, (deadline_ns - time_ns()) // 1_000_000)
            if not sp.force_flush(timeout_millis=remaining_ms):
                return False
        return True


try:
    from opentelemetry.util._decorator import _agnosticcontextmanager as contextmanager  # type: ignore[no-redef]
except ImportError:
    log.warning(
        "opentelemetry.util._decorator not found, using contextlib.contextmanager instead. "
        "Using @tracer.start_as_current_span decorator in generators and async functions "
        "will result in inaccurate durations. For async support upgrade to opentelemetry-api>=1.24."
    )
    from contextlib import contextmanager


OTEL_VERSION = tuple(int(x) for x in version.__version__.split(".")[:3])


def _otel_to_dd_span_context(otel_span: "OtelSpan") -> "DDContext":
    trace_id, span_id, _, tf, ts, _ = otel_span.get_span_context()
    if tf is DEFAULT_TRACE_OPTIONS:
        # If a SpanContext is created with specificing the trace flags field it is set to DEFAULT_TRACE_OPTIONS.
        # This is analogous to setting SamplingPriority to None. The span will be sampled lazily.
        tf = None
    trace_state = ts.to_header()
    return _TraceContext._get_context(trace_id, span_id, tf, trace_state)


class TracerProvider(OtelTracerProvider):
    """
    Entry point of the OpenTelemetry API and provides access to OpenTelemetry compatible Tracers.
    One TracerProvider should be initialized and set per application.
    """

    def __init__(self) -> None:
        self._active_span_processor = SynchronousMultiSpanProcessor()

    def add_span_processor(self, span_processor: SpanProcessor) -> None:
        """Registers a :class:`SpanProcessor` with this ``TracerProvider``.

        Processors are called synchronously in the order they were added.
        Mirrors ``opentelemetry.sdk.trace.TracerProvider.add_span_processor``.
        """
        self._active_span_processor.add_span_processor(span_processor)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        """Flushes all registered span processors.

        Mirrors ``opentelemetry.sdk.trace.TracerProvider.force_flush``.
        """
        return self._active_span_processor.force_flush(timeout_millis)

    if OTEL_VERSION >= (1, 26):
        # OpenTelemetry 1.26+ has a new get_tracer signature
        # https://github.com/open-telemetry/opentelemetry-python/commit/d4e13bdf95190314b0d21a9357f850fa2e6a4cd3
        # The new signature includes an `attributes` parameter which is used by opentelemetry internals.
        def get_tracer(
            self,
            instrumenting_module_name: str,
            instrumenting_library_version: Optional[str] = None,
            schema_url: Optional[str] = None,
            attributes: Optional[dict] = None,
        ) -> OtelTracer:
            """Returns an opentelemetry compatible Tracer."""
            return Tracer(self._active_span_processor)

    else:

        def get_tracer(  # type: ignore[misc]
            self,
            instrumenting_module_name: str,
            instrumenting_library_version: Optional[str] = None,
            schema_url: Optional[str] = None,
        ) -> OtelTracer:
            """Returns an opentelemetry compatible Tracer."""
            return Tracer(self._active_span_processor)


class Tracer(OtelTracer):
    """Starts and/or activates OpenTelemetry compatible Spans using the global Datadog Tracer."""

    def __init__(self, active_span_processor: Optional[SynchronousMultiSpanProcessor] = None) -> None:
        self._active_span_processor = active_span_processor

    def start_span(
        self,
        name: str,
        context: Optional[OtelContext] = None,
        kind: OtelSpanKind = OtelSpanKind.INTERNAL,
        attributes: Optional[Mapping[str, "OtelAttributeValue"]] = None,
        links: Optional[Sequence["OtelLink"]] = None,
        start_time: Optional[int] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> "OtelSpan":
        """Creates and starts an opentelemetry span."""
        # Get active otel span
        curr_otel_span = get_current_span(context)
        if curr_otel_span is INVALID_SPAN:
            # There is no active datadog/otel span
            dd_active: Optional[DDActiveTrace] = None
        elif isinstance(curr_otel_span, Span):
            # Get underlying ddtrace span from the active otel span
            dd_active = curr_otel_span._ddspan
        else:
            # Otel span was not generated by the ddtrace library and does not have an underlying ddtrace span.
            # Convert otel span to a ddtrace context object.
            dd_active = _otel_to_dd_span_context(curr_otel_span)

        # Create a new Datadog span (not activated), then return a valid OTel span
        dd_span = ddtracer._start_span(name, child_of=dd_active, activate=False, span_api=SPAN_API_OTEL)

        if links:
            for link in links:
                dd_span.set_link(
                    link.context.trace_id,
                    link.context.span_id,
                    link.context.trace_state.to_header(),
                    link.context.trace_flags,
                    link.attributes,
                )
        return Span(
            dd_span,
            kind=kind,
            attributes=attributes,
            start_time=start_time,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
            span_processor=self._active_span_processor,
            parent_context=context,
        )

    @contextmanager
    def start_as_current_span(
        self,
        name: str,
        context: Optional[OtelContext] = None,
        kind: OtelSpanKind = OtelSpanKind.INTERNAL,
        attributes: Optional[Mapping[str, "OtelAttributeValue"]] = None,
        links: Optional[Sequence["OtelLink"]] = None,
        start_time: Optional[int] = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
        end_on_exit: bool = True,
    ) -> Iterator["OtelSpan"]:
        """Context manager for creating and activating a new opentelemetry span."""
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
