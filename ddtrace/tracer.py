import functools
import logging
import threading

from .buffer import ThreadLocalSpanBuffer
from .sampler import AllSampler
from .span import Span
from .writer import AgentWriter


log = logging.getLogger(__name__)


class Tracer(object):
    """ Tracer is used to create, sample and submit spans that measure the
        execution time of sections of code.

        If you're running an application that will serve a single trace per thread,
        you can use the global traced instance:

        >>> from ddtrace import tracer
        >>> tracer.trace("foo").finish()
    """
    def __init__(self):
        """Create a new tracer."""

        # Apply the default configuration
        self.enabled = True
        self.writer = AgentWriter()
        self.sampler = AllSampler()

        # a list of buffered spans.
        self._spans_lock = threading.Lock()
        self._spans = []

        # track the active span
        self.span_buffer = ThreadLocalSpanBuffer()

        # a collection of registered services by name.
        self._services = {}

        # A hook for local debugging. shouldn't be needed or used
        # in production.
        self.debug_logging = False

        # start background workers to send data to a trace agent
        self.writer.start()

    def configure(self, enabled=None, hostname=None, port=None, sampler=None, buffer_size=None, flush_interval=None, service_interval=None):
        """
        Configure an existing Tracer the easy way. Allow to configure or reconfigure a ``Tracer`` instance.

        :param bool enabled: If True, finished traces will be submitted to the API.
                             Otherwise they'll be dropped.
        :param str hostname: Hostname running the Trace Agent
        :param int port: Port of the Trace Agent
        :param object sampler: A custom Sampler instance
        """
        # set the tracer
        if enabled is not None:
            self.enabled = enabled

        # set the sampler
        if sampler is not None:
            self.sampler = sampler

        params = {}
        if buffer_size is not None:
            params.update({'buffer_size': buffer_size})

        if flush_interval is not None:
            params.update({'flush_interval': flush_interval})

        if service_interval is not None:
            params.update({'service_interval': service_interval})

        # initialize the writer if an internal has been changed
        if params:
            # stop any running workers
            self.writer.stop()
            # create and start workers again
            self.writer = AgentWriter(**params)
            self.writer.start()

        # these values don't need to recreate a writer again
        if hostname is not None:
            self.writer._transport.hostname = hostname

        if port is not None:
            self.writer._transport.port = port

    def trace(self, name, service=None, resource=None, span_type=None):
        """Return a span that will trace an operation called `name`.

        :param str name: the name of the operation being traced
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from it's parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        You must call `finish` on all spans, either directly or with a context
        manager.

        >>> span = tracer.trace("web.request")
            try:
                # do something
            finally:
                span.finish()
        >>> with tracer.trace("web.request") as span:
            # do something

        Trace will store the created span and subsequent child traces will
        become it's children.

        >>> tracer = Tracer()
        >>> parent = tracer.trace("parent")     # has no parent span
        >>> child  = tracer.trace("child")      # is a child of a parent
        >>> child.finish()
        >>> parent.finish()
        >>> parent2 = tracer.trace("parent2")   # has no parent span
        >>> parent2.finish()
        """
        span = None
        parent = self.span_buffer.get()

        if parent:
            # if we have a current span link the parent + child nodes.
            span = Span(
                self,
                name,
                service=(service or parent.service),
                resource=resource,
                span_type=span_type,
                trace_id=parent.trace_id,
                parent_id=parent.span_id,
            )
            span._parent = parent
            span.sampled = parent.sampled
        else:
            span = Span(
                self,
                name,
                service=service,
                resource=resource,
                span_type=span_type,
            )
            self.sampler.sample(span)

        # Note the current trace.
        self.span_buffer.set(span)

        return span

    def current_span(self):
        """Return the current active span or None."""
        return self.span_buffer.get()

    def clear_current_span(self):
        self.span_buffer.pop()

    def record(self, span):
        """Record the given finished span."""
        spans = []
        with self._spans_lock:
            self._spans.append(span)
            parent = span._parent
            self.span_buffer.set(parent)
            if not parent:
                spans = self._spans
                self._spans = []

        if spans and span.sampled:
            self.write(spans)

    def write(self, spans):
        if not spans:
            return  # nothing to do

        if self.debug_logging:
            log.debug("writing %s spans (enabled:%s)", len(spans), self.enabled)
            for span in spans:
                log.debug("\n%s", span.pprint())

        if self.enabled and self.writer:
            # only submit the spans if we're actually enabled (and don't crash :)
            self.writer.write_trace(spans)

    def set_service_info(self, service, app, app_type):
        """Set the information about the given service.

        :param str service: the internal name of the service (e.g. acme_search, datadog_web)
        :param str app: the off the shelf name of the application (e.g. rails, postgres, custom-app)
        :param str app_type: the type of the application (e.g. db, web)
        """
        self._services[service] = {
            "app" : app,
            "app_type": app_type,
        }

        if self.debug_logging:
            log.debug("set_service_info: service:%s app:%s type:%s",
                service, app, app_type)

        if self.enabled and self.writer:
            self.writer.write_service(self._services)

    def wrap(self, name=None, service=None, resource=None, span_type=None):
        """A decorator used to trace an entire function.

        :param str name: the name of the operation being traced. If not set,
                         defaults to the fully qualified function name.
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from it's parent.
        :param str resource: an optional name of the resource being tracked.
        :param str span_type: an optional operation type.

        >>> @tracer.wrap('my.wrapped.function', service='my.service')
            def run():
                return 'run'
        >>> @tracer.wrap()  # name will default to 'execute' if unset
            def execute():
                return 'executed'

        You can access the parent span using `tracer.current_span()` to set
        tags:

        >>> @tracer.wrap()
            def execute():
                span = tracer.current_span()
                span.set_tag('a', 'b')
        """

        def wrap_decorator(f):

            # FIXME[matt] include the class name for methods.
            span_name = name if name else '%s.%s' % (f.__module__, f.__name__)

            @functools.wraps(f)
            def func_wrapper(*args, **kwargs):
                with self.trace(span_name, service=service, resource=resource, span_type=span_type):
                    return f(*args, **kwargs)
            return func_wrapper

        return wrap_decorator
