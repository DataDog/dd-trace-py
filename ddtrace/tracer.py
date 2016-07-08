
import logging
import threading

from .buffer import ThreadLocalSpanBuffer
from .sampler import DefaultSampler
from .span import Span
from .writer import AgentWriter


log = logging.getLogger(__name__)


class Tracer(object):
    """
    Tracer is used to create, sample and submit spans that measure the
    execution time of sections of code.

    If you're running an application that will serve a single trace per thread,
    you can use the global traced instance:

    >>> from ddtrace import tracer
    >>> tracer.trace("foo").finish()
    """

    def __init__(self, enabled=True, writer=None, span_buffer=None, sampler=None):
        """
        Create a new tracer.

        :param bool enabled: If true, finished traces will be submitted to the API. Otherwise they'll be dropped.
        """
        self.enabled = enabled

        self._writer = writer or AgentWriter()
        self._span_buffer = span_buffer or ThreadLocalSpanBuffer()
        self.sampler = sampler or DefaultSampler()

        # a list of buffered spans.
        self._spans_lock = threading.Lock()
        self._spans = []

        # a collection of registered services by name.
        self._services = {}

        # A hook for local debugging. shouldn't be needed or used
        # in production.
        self.debug_logging = False

    def trace(self, name, service=None, resource=None, span_type=None):
        """
        Return a span that will trace an operation called `name`.

        :param str name: the name of the operation being traced
        :param str service: the name of the service being traced. If not set,
                            it will inherit the service from it's parent.
        :param str resource: an optional name of the resource being tracked.

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
        parent = self._span_buffer.get()

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
        self._span_buffer.set(span)

        return span

    def current_span(self):
        """ Return the current active span or None. """
        return self._span_buffer.get()

    def record(self, span):
        """ Record the given finished span. """
        spans = []
        with self._spans_lock:
            self._spans.append(span)
            parent = span._parent
            self._span_buffer.set(parent)
            if not parent:
                spans = self._spans
                self._spans = []

        if self._writer and span.sampled:
            self.write(spans)

    def write(self, spans):
        if not spans:
            return  # nothing to do

        if self.debug_logging:
            log.debug("writing %s spans (enabled:%s)", len(spans), self.enabled)
            for span in spans:
                log.debug("\n%s", span.pprint())

        if self.enabled:
            # only submit the spans if we're actually enabled.
            self._writer.write(spans, self._services)

    def set_service_info(self, service, app, app_type):
        """
        Set the information about the given service.

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

