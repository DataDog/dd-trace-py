import logging
import threading

from .constants import HOSTNAME_KEY, SAMPLING_PRIORITY_KEY, ORIGIN_KEY, LOG_SPAN_KEY
from .internal.logger import get_logger
from .internal import hostname
from .settings import config
from .utils.formats import asbool, get_env

log = get_logger(__name__)


class Context(object):
    """
    Context is used to keep track of a hierarchy of spans for the current
    execution flow. During each logical execution, the same ``Context`` is
    used to represent a single logical trace, even if the trace is built
    asynchronously.

    A single code execution may use multiple ``Context`` if part of the execution
    must not be related to the current tracing. As example, a delayed job may
    compose a standalone trace instead of being related to the same trace that
    generates the job itself. On the other hand, if it's part of the same
    ``Context``, it will be related to the original trace.

    This data structure is thread-safe.
    """

    _partial_flush_enabled = asbool(get_env("tracer", "partial_flush_enabled", default=False))
    _partial_flush_min_spans = int(get_env("tracer", "partial_flush_min_spans", default=500))

    def __init__(self, trace_id=None, span_id=None, sampling_priority=None, _dd_origin=None):
        """
        Initialize a new thread-safe ``Context``.

        :param int trace_id: trace_id of parent span
        :param int span_id: span_id of parent span
        """
        self._trace = []
        self._finished_spans = 0
        self._current_span = None
        self._lock = threading.Lock()

        self._parent_trace_id = trace_id
        self._parent_span_id = span_id
        self._sampling_priority = sampling_priority
        self._dd_origin = _dd_origin

    @property
    def trace_id(self):
        """Return current context trace_id."""
        with self._lock:
            return self._parent_trace_id

    @property
    def span_id(self):
        """Return current context span_id."""
        with self._lock:
            return self._parent_span_id

    @property
    def sampling_priority(self):
        """Return current context sampling priority."""
        with self._lock:
            return self._sampling_priority

    @sampling_priority.setter
    def sampling_priority(self, value):
        """Set sampling priority."""
        with self._lock:
            self._sampling_priority = value

    def clone(self):
        """
        Partially clones the current context.
        It copies everything EXCEPT the registered and finished spans.
        """
        with self._lock:
            new_ctx = Context(
                trace_id=self._parent_trace_id,
                span_id=self._parent_span_id,
                sampling_priority=self._sampling_priority,
            )
            new_ctx._current_span = self._current_span
            return new_ctx

    def get_current_root_span(self):
        """
        Return the root span of the context or None if it does not exist.
        """
        return self._trace[0] if len(self._trace) > 0 else None

    def get_current_span(self):
        """
        Return the last active span that corresponds to the last inserted
        item in the trace list. This cannot be considered as the current active
        span in asynchronous environments, because some spans can be closed
        earlier while child spans still need to finish their traced execution.
        """
        with self._lock:
            return self._current_span

    def _set_current_span(self, span):
        """
        Set current span internally.

        Non-safe if not used with a lock. For internal Context usage only.
        """
        self._current_span = span
        if span:
            self._parent_trace_id = span.trace_id
            self._parent_span_id = span.span_id
        else:
            self._parent_span_id = None

    def add_span(self, span):
        """
        Add a span to the context trace list, keeping it as the last active span.
        """
        with self._lock:
            self._set_current_span(span)

            self._trace.append(span)
            span._context = self

    def close_span(self, span):
        """
        Mark a span as a finished, increasing the internal counter to prevent
        cycles inside _trace list.
        """
        with self._lock:
            self._finished_spans += 1

            # Safe-guard: prevent the last current span from being set to the parent
            # of any span but the top-level span.
            # The situation this avoids is when a parent closes before a child
            # and the child is the last to close in the trace. When this happens
            # the current_span would otherwise be set to the child's parent which
            # has already closed. The context will be reset but the current_span
            # will still point to that child's parent which would cause subsequent
            # spans to be parented incorrectly.
            if self._finished_spans != len(self._trace) or span == self._trace[0]:
                self._set_current_span(span._parent)

            # notify if the trace is not closed properly; this check is executed only
            # if the debug logging is enabled and when the root span is closed
            # for an unfinished trace. This logging is meant to be used for debugging
            # reasons, and it doesn't mean that the trace is wrongly generated.
            # In asynchronous environments, it's legit to close the root span before
            # some children. On the other hand, asynchronous web frameworks still expect
            # to close the root span after all the children.
            if span.tracer and span.tracer.log.isEnabledFor(logging.DEBUG) and span._parent is None:
                extra = {LOG_SPAN_KEY: span}
                unfinished_spans = [x for x in self._trace if not x.finished]
                if unfinished_spans:
                    log.debug(
                        'Root span "%s" closed, but the trace has %d unfinished spans:',
                        span.name,
                        len(unfinished_spans),
                        extra=extra,
                    )
                    for wrong_span in unfinished_spans:
                        log.debug("\n%s", wrong_span.pprint(), extra=extra)

            if self._finished_spans == len(self._trace):
                # get the trace
                trace = self._trace
                sampled = self._is_sampled()
                sampling_priority = self._sampling_priority
                # attach the sampling priority to the context root span
                if sampled and sampling_priority is not None and trace:
                    trace[0].set_metric(SAMPLING_PRIORITY_KEY, sampling_priority)
                origin = self._dd_origin
                # attach the origin to the root span tag
                if sampled and origin is not None and trace:
                    trace[0].meta[ORIGIN_KEY] = str(origin)

                # Set hostname tag if they requested it
                if config.report_hostname:
                    # DEV: `get_hostname()` value is cached
                    trace[0].meta[HOSTNAME_KEY] = hostname.get_hostname()

                # clean the current state
                self._trace = []
                self._finished_spans = 0
                self._parent_trace_id = None
                self._parent_span_id = None
                self._sampling_priority = None
                return trace, sampled
            elif self._partial_flush_enabled:
                finished_spans = [t for t in self._trace if t.finished]
                if len(finished_spans) >= self._partial_flush_min_spans:
                    # partial flush when enabled and we have more than the minimal required spans
                    trace = self._trace
                    sampled = self._is_sampled()
                    sampling_priority = self._sampling_priority
                    # attach the sampling priority to the context root span
                    if sampled and sampling_priority is not None and trace:
                        trace[0].set_metric(SAMPLING_PRIORITY_KEY, sampling_priority)
                    origin = self._dd_origin
                    # attach the origin to the root span tag
                    if sampled and origin is not None and trace:
                        trace[0].meta[ORIGIN_KEY] = str(origin)

                    # Set hostname tag if they requested it
                    if config.report_hostname:
                        # DEV: `get_hostname()` value is cached
                        trace[0].meta[HOSTNAME_KEY] = hostname.get_hostname()

                    self._finished_spans = 0

                    # Any open spans will remain as `self._trace`
                    # Any finished spans will get returned to be flushed
                    self._trace = [t for t in self._trace if not t.finished]
                    return finished_spans, sampled
            return None, None

    def _is_sampled(self):
        return any(span.sampled for span in self._trace)
