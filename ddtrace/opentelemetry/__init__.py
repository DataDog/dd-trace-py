from contextlib import contextmanager
from typing import Iterator
from typing import Optional
from typing import Sequence
from typing import Union

from opentelemetry.context import _RuntimeContext
from opentelemetry.trace import Link
from opentelemetry.trace import Span as OTelSpan
from opentelemetry.trace import SpanContext
from opentelemetry.trace import SpanKind
from opentelemetry.trace import Status
from opentelemetry.trace import StatusCode
from opentelemetry.trace import Tracer as OTelTracer
from opentelemetry.trace import TracerProvider as OTelTracerProvider
from opentelemetry.trace import get_current_span
from opentelemetry.trace import use_span
from opentelemetry.trace.span import DEFAULT_TRACE_OPTIONS
from opentelemetry.trace.span import DEFAULT_TRACE_STATE
from opentelemetry.util import types

import ddtrace
from ddtrace.context import Context
from ddtrace.provider import DefaultContextProvider


class TracerProvider(OTelTracerProvider):
    def get_tracer(
        self,
        instrumenting_module_name,  # type: str
        instrumenting_library_version=None,  # type: Optional[str]
        schema_url=None,  # type: Optional[str]
        datadog_tracer=None,  # type: Optional[ddtrace.Tracer]
    ):
        # type: (...) -> Tracer
        """Returns a `Tracer` for use by the given instrumentation library.
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
        return Tracer(datadog_tracer=datadog_tracer)


class Tracer(OTelTracer):
    def __init__(self, datadog_tracer=None):
        # (Optional[ddtrace.Tracer]) -> None

        # Use the tracer provided or the global tracer
        self._tracer = datadog_tracer or ddtrace.tracer

    def start_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[SpanContext]
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: types.Attributes
        links=None,  # type: Optional[Sequence[Link]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
    ):
        # type: (...) -> Span
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
            dd_context = Context(context.trace_id, context.span_id)
        else:
            dd_context = (
                self._tracer.context_provider.active()
            )  #  type: Optional[Union[ddtrace.context.Context, ddtrace.Span]]
        # Create a new Datadog span (not activated), then return an OTel span wrapper
        dd_span = self._tracer.start_span(name, child_of=dd_context, activate=False)
        return Span(dd_span, kind=kind, attributes=attributes, start_time=start_time)

    @contextmanager
    def start_as_current_span(
        self,
        name,  # type: str
        context=None,  # type: Optional[Context]
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: types.Attributes
        links=None,  # type: Optional[Sequence[Link]]
        start_time=None,  # type: Optional[int]
        record_exception=True,  # type: bool
        set_status_on_exception=True,  # type: bool
        end_on_exit=True,  # type: bool
    ):
        # type: (...) -> Iterator[Span]
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


class Span(OTelSpan):
    def __init__(
        self,
        datadog_span,  # type: ddtrace.Span
        kind=SpanKind.INTERNAL,  # type: SpanKind
        attributes=None,  # type: Optional[types.Attributes]
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
        # type: (Dict[str, types.AttributeValue]) -> None
        """Sets Attributes.
        Sets Attributes with the key and value passed as arguments dict.
        Note: The behavior of `None` value attributes is undefined, and hence
        strongly discouraged. It is also preferred to set attributes at span
        creation, instead of calling this method later since samplers can only
        consider information already present during span creation.
        """
        self._span.set_tags(attributes)

    def set_attribute(self, key, value):
        # type: (str, types.AttributeValue) -> None
        """Sets an Attribute.
        Sets a single Attribute with the key and value passed as arguments.
        Note: The behavior of `None` value attributes is undefined, and hence
        strongly discouraged. It is also preferred to set attributes at span
        creation, instead of calling this method later since samplers can only
        consider information already present during span creation.
        """
        self._span.set_tag(key, value)

    def add_event(self, name, attributes=None, timestamp=None):
        # type: (str, types.Attributes, Optional[int]) -> None
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
        # type: (Exception, types.Attributes, Optional[int], bool) -> None
        """Records an exception as a span event."""
        return


class _OtelRuntimeContext(_RuntimeContext, DefaultContextProvider):
    """The RuntimeContext interface provides a wrapper for the different
    mechanisms that are used to propagate context in Python.
    Implementations can be made available via entry_points and
    selected through environment variables.
    """

    def __init__(self):
        super(_OtelRuntimeContext, self).__init__()

    def attach(self, context: SpanContext) -> object:
        """Sets the current `Context` object. Returns a
        token that can be used to reset to the previous `Context`.
        Args:
            context: The Context to set.
        """
        # The API MUST return a value that can be used as a Token to restore the previous Context.
        dd_context = Context(context.trace_id, context.span_id)
        return self.activate(dd_context)

    def get_current(self) -> SpanContext:
        """Returns the current `Context` object."""
        item = self.active()
        if isinstance(item, Span):
            return SpanContext(item.trace_id, item.span_id, False, DEFAULT_TRACE_OPTIONS, DEFAULT_TRACE_STATE)
        elif isinstance(item, Context):
            return SpanContext(item.trace_id, item.span_id, False, DEFAULT_TRACE_OPTIONS, DEFAULT_TRACE_STATE)
        return SpanContext()

    def detach(self, token: object) -> None:
        """Resets Context to a previous value
        Args:
            token: A reference to a previous Context.
        """
        pass
        # self._current_context.reset(token)
