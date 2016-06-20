
import logging
import threading

from .buffer import ThreadLocalSpanBuffer
from .span import Span
from .writer import AgentWriter


log = logging.getLogger(__name__)


class Tracer(object):

    def __init__(self, writer=None, span_buffer=None):
        self._writer = writer or AgentWriter()
        self._span_buffer = span_buffer or ThreadLocalSpanBuffer()

        self._spans_lock = threading.Lock()
        self._spans = []

        self.enabled = True
        self.debug_logging = False

    def trace(self, name, service=None, resource=None):
        """
        Return a span that will trace an operation called `name`.
        """
        # if we have a current span link the parent + child nodes.
        parent = self._span_buffer.get()
        trace_id, parent_id = None, None
        if parent:
            trace_id, parent_id = parent.trace_id, parent.span_id

        # Create the trace.
        span = Span(self, name, service=service, trace_id=trace_id, parent_id=parent_id)
        if parent:
            span._parent = parent
            span.service = span.service or parent.service # inherit service if unset

        # Note the current trace.
        self._span_buffer.set(span)

        return span

    def current_span(self):
        return self._span_buffer.get()

    def record(self, span):
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
                if self.debug_logging:
                    # A hook for local debugging. shouldn't be needed or used
                    # in production.
                    log.debug("submitting %s spans", len(spans))
                    for span in spans:
                        log.debug(span.pprint())

                self._writer.write(spans)
