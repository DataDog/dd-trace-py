
import logging
import threading

from .buffer import ThreadLocalSpanBuffer
from .span import Span
from .writer import AgentWriter


log = logging.getLogger(__name__)


class Tracer(object):

    def __init__(self, enabled=True, writer=None, span_buffer=None):
        """
        Create a new tracer object.

        enabled: if False, no spans will be submitted to the writer.

        writer: an instance of Writer
        span_buffer: a span buffer instance. used to store inflight traces. by
                     default, will use thread local storage.
        """
        self._writer = writer or AgentWriter()
        self._span_buffer = span_buffer or ThreadLocalSpanBuffer()

        # a list of buffered spans.
        self._spans_lock = threading.Lock()
        self._spans = []

        self.enabled = enabled

        # A hook for local debugging. shouldn't be needed or used
        # in production.
        self.debug_logging = False

    def trace(self, name, service=None, resource=None, span_type=None):
        """
        Return a span that will trace an operation called `name`.

        It will store the created span in the span buffer and until it's
        finished, any new spans will be a child of this span.

        >>> tracer = Tracer()
        >>> parent = tracer.trace("parent") # has no parent span
        >>> child  = tracer.child("child")  # is a child of a parent
        >>> child.finish()
        >>> parent.finish()
        >>> parent2 = tracer.trace("parent2") # has no parent span
        """
        # if we have a current span link the parent + child nodes.
        parent = self._span_buffer.get()
        trace_id, parent_id = None, None
        if parent:
            trace_id, parent_id = parent.trace_id, parent.span_id

        # Create the trace.
        span = Span(self,
            name,
            service=service,
            resource=resource,
            trace_id=trace_id,
            parent_id=parent_id,
            span_type=span_type,
        )

        # if there's a parent, link them and inherit the service.
        if parent:
            span._parent = parent
            span.service = span.service or parent.service

        # Note the current trace.
        self._span_buffer.set(span)

        return span

    def current_span(self):
        """ Return the current active span or None. """
        return self._span_buffer.get()

    def record(self, span):
        """ Record the given finished span. """
        if not self.enabled:
            return

        if self._writer:
            spans = None
            with self._spans_lock:
                self._spans.append(span)
                parent = span._parent
                self._span_buffer.set(parent)
                if not parent:
                    spans = self._spans
                    self._spans = []

            if spans:
                self.write(spans)

    def write(self, spans):
        """ Submit the given spans to the agent. """
        if spans:
            if self.debug_logging:
                log.info("submitting %s spans", len(spans))
                for span in spans:
                    log.info("\n%s" % span.pprint())

            self._writer.write(spans)
