from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry.trace import SpanContext
from opentelemetry.trace import SpanKind
from opentelemetry.trace import Tracer
from opentelemetry.trace import TracerProvider
from opentelemetry.trace import use_span

from ddtrace import tracer as ddtracer
from ddtrace.context import Context as DDContext
from ddtrace.opentelemetry.span import OtelSpan


if TYPE_CHECKING:
    from typing import Iterator
    from typing import Optional
    from typing import Sequence

    from opentelemetry.trace import Link
    from opentelemetry.util.types import Attributes

    from ddtrace.tracer import Tracer as DDTracer


class OtelTracerProvider(TracerProvider):
    def get_tracer(
        self,
        instrumenting_module_name,  # type: str
        instrumenting_library_version=None,  # type: Optional[str]
        schema_url=None,  # type: Optional[str]
        datadog_tracer=None,  # type: Optional[DDTracer]
    ):
        # type: (...) -> OtelTracer
        """Returns a `OtelTracer` for use by the given instrumentation library.
        For any two calls it is undefined whether the same or different
        `Tracer` instances are returned, even for different library names.
        This function may return different `Tracer` types (e.g. a no-op tracer
        vs.  a functional tracer).
        Args:
            instrumenting_module_name: The uniquely identifiable name for instrumentation
                scope, such as instrumentation library, package, module or class name.
                ``__name__`` may not be used as this can result in
                different tracer names if the tracers are in different files.
                It is better to use a fixed string that can be imported where
                needed and used consistently as the name of the tracer.
                this should *not* be the name of the module that is
                instrumented but the name of the module doing the instrumentation.
                E.g., instead of ``"requests"``, use
                ``"opentelemetry.instrumentation.requests"``.
            instrumenting_library_version: Optional. The version string of the
                instrumenting library.  Usually this should be the same as
                ``pkg_resources.get_distribution(instrumenting_library_name).version``.
            schema_url: Optional. Specifies the Schema URL of the emitted telemetry.
        """
        # TODO: Do we need to do anything else with the other arguments?
        return OtelTracer(datadog_tracer=datadog_tracer)


class OtelTracer(Tracer):
    def __init__(self, datadog_tracer=None):
        # (Optional[ddtrace.Tracer]) -> None

        # Use the tracer provided or the global tracer
        self._tracer = datadog_tracer or ddtracer
        super(Tracer, self).__init__()

    def start_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[SpanContext]
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: Optional[Attributes]
        links=None,  # type: Optional[Sequence[Link]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
    ):
        # type: (...) -> OtelSpan
        """Starts a span.
        Create a new span. Start the span without setting it as the current
        span in the context. To start the span and use the context in a single
        method, see :meth:`start_as_current_span`.
        By default the current span in the context will be used as parent, but an
        explicit context can also be specified, by passing in a `Context` containing
        a current `Span`. If there is no current span in the global `Context` or in
        the specified context, the created span will be a root span.
        The span can be used as a context manager. On exiting the context manager,
        the span's end() method will be called.
        Example::
            # trace.get_current_span() will be used as the implicit parent.
            # If none is found, the created span will be a root instance.
            with tracer.start_span("one") as child:
                child.add_event("child's event")
        Args:
            name: The name of the span to be created.
            context: An optional Context containing the span's parent. Defaults to the
                global context.
            kind: The span's kind (relationship to parent). Note that is
                meaningful even if there is no parent.
            attributes: The span's attributes.
            links: Links span to other spans
            start_time: Sets the start time of a span
            record_exception: Whether to record any exceptions raised within the
                context as error event on the span.
            set_status_on_exception: Only relevant if the returned span is used
                in a with/context manager. Defines whether the span status will
                be automatically set to ERROR when an uncaught exception is
                raised in the span with block. The span status won't be set by
                this mechanism if it was previously set manually.
        Returns:
            The newly-created span.
        """
        # End result is to get a Datadog Context/Span to use as the parent (or None)
        if context:
            dd_context = DDContext(context.trace_id, context.span_id)
        else:
            dd_context = self._tracer.context_provider.active()
        # Create a new Datadog span (not activated), then return an OTel span wrapper
        dd_span = self._tracer.start_span(name, child_of=dd_context, activate=False)
        return OtelSpan(dd_span, kind=kind, attributes=attributes, start_time=start_time)

    @contextmanager
    def start_as_current_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[DDContext]
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: Attributes
        links=None,  # type: Optional[Sequence[Link]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
        end_on_exit=True,  # type: bool
    ):
        # type: (...) -> Iterator[OtelSpan]
        """Context manager for creating a new span and set it
        as the current span in this tracer's context.
        Exiting the context manager will call the span's end method,
        as well as return the current span to its previous value by
        returning to the previous context.
        Example::
            with tracer.start_as_current_span("one") as parent:
                parent.add_event("parent's event")
                with trace.start_as_current_span("two") as child:
                    child.add_event("child's event")
                    trace.get_current_span()  # returns child
                trace.get_current_span()      # returns parent
            trace.get_current_span()          # returns previously active span
        This is a convenience method for creating spans attached to the
        tracer's context. Applications that need more control over the span
        lifetime should use :meth:`start_span` instead. For example::
            with tracer.start_as_current_span(name) as span:
                do_work()
        is equivalent to::
            span = tracer.start_span(name)
            with opentelemetry.trace.use_span(span, end_on_exit=True):
                do_work()
        This can also be used as a decorator::
            @tracer.start_as_current_span("name"):
            def function():
                ...
            function()
        Args:
            name: The name of the span to be created.
            context: An optional Context containing the span's parent. Defaults to the
                global context.
            kind: The span's kind (relationship to parent). Note that is
                meaningful even if there is no parent.
            attributes: The span's attributes.
            links: Links span to other spans
            start_time: Sets the start time of a span
            record_exception: Whether to record any exceptions raised within the
                context as error event on the span.
            set_status_on_exception: Only relevant if the returned span is used
                in a with/context manager. Defines whether the span status will
                be automatically set to ERROR when an uncaught exception is
                raised in the span with block. The span status won't be set by
                this mechanism if it was previously set manually.
            end_on_exit: Whether to end the span automatically when leaving the
                context manager.
        Yields:
            The newly-created span.
        """
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

        # Activate the span in the Datadog context manager
        self._tracer.context_provider.activate(span._span)

        # Activate the span in the OTel context manager
        # DEV: This is needed to ensure the rest of the OTel trace APIs work
        with use_span(
            span,
            end_on_exit=end_on_exit,
            record_exception=record_exception,
            set_status_on_exception=set_status_on_exception,
        ) as span_context:
            yield span_context
