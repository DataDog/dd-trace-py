"""
FIXME: This class should be deeply changed because it doesn't handle
async code; keeping it as a reference of @nico work. Will be removed
later.
"""

import logging
import threading


log = logging.getLogger(__name__)


class Context(object):
    """
    Context is used to keep track of a hierarchy of spans.
    """

    def __init__(self):
        """
        Initialize a new context.
        """
        self._lock = threading.Lock()
        self.clear()

    def __str__(self):
        return "<Context {0}>".format(id(self))

    def add_span(self, span):
        """
        Add a span to the context. If the context is not empty, the new span
        is added as a child of the current span.
        """
        with self._lock:
            parent = self.current_span

            # log.debug("{0}: add {1} as child of {2}".format(self, span, parent))

            if parent is not None:
                span._parent = parent
                span.parent_id = parent.span_id
                span.trace_id = parent.trace_id
                span.sampled = parent.sampled
                if span.service is None:
                    span.service = parent.service

            self.current_span = span

    def finish_span(self, span):
        """
        Mark a span as finished. The current span is set to the first
        non-finished parent.
        Return True if the root span, i.e. the span whose parent is None, just
        finished, or False else.
        """
        with self._lock:
            # log.debug("{0}: finish {1}".format(self, span))

            self.finished_spans.append(span)

            parent = span._parent
            self.current_span = parent

            return parent is None

    def clear(self):
        """
        Clear the context, removing all the spans it contains.
        """
        with self._lock:
            # log.debug("{0}: clear".format(self))

            self.finished_spans = []
            self.current_span = None
