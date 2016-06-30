import logging
import threading

from .buffer import ThreadLocalSpanBuffer
from .sampler import RateSampler
from .span import Span
from .writer import AgentWriter


log = logging.getLogger(__name__)


class Tracer(object):

    def __init__(self, enabled=True, writer=None, span_buffer=None, sample_rate=1):
        """
        Create a new tracer object.

        enabled: if False, no spans will be submitted to the writer
        writer: an instance of Writer
        span_buffer: a span buffer instance. used to store inflight traces. by
                     default, will use thread local storage
        sample_rate: Pre-sampling rate.
        """
        self.enabled = enabled

        self._writer = writer or AgentWriter()
        self._span_buffer = span_buffer or ThreadLocalSpanBuffer()

        # a list of buffered spans.
        self._spans_lock = threading.Lock()
        self._spans = []

        self.sampler = RateSampler(sample_rate)

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
        span = None
        parent = self._span_buffer.get()

        if parent:
            # if we have a current span link the parent + child nodes.
            span = Span(
                self,
                name,
                trace_id=parent.trace_id,
                parent_id=parent.span_id,
                service=(service or parent.service),
                resource=resource,
                span_type=span_type,
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
        if not self.enabled:
            return

        spans = []
        with self._spans_lock:
            self._spans.append(span)
            parent = span._parent
            self._span_buffer.set(parent)
            if not parent:
                spans = self._spans
                self._spans = []

        if self._writer and not span.sampled:
            self.write(spans)

    def write(self, spans):
        """ Submit the given spans to the agent. """
        if spans:
            if self.debug_logging:
                log.debug("submitting %s spans", len(spans))
                for span in spans:
                    log.debug("\n%s", span.pprint())

            self._writer.write(spans)
