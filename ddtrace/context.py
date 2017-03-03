import logging
import threading


log = logging.getLogger(__name__)


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
    def __init__(self):
        """
        Initialize a new thread-safe ``Context``.
        """
        self._trace = []
        self._sampled = False
        self._finished_spans = 0
        self._current_span = None
        self._lock = threading.Lock()

    def get_current_span(self):
        """
        Return the last active span that corresponds to the last inserted
        item in the trace list. This cannot be considered as the current active
        span in asynchronous environments, because some spans can be closed
        earlier while child spans still need to finish their traced execution.
        """
        with self._lock:
            return self._current_span

    def add_span(self, span):
        """
        Add a span to the context trace list, keeping it as the last active span.
        """
        with self._lock:
            self._current_span = span
            self._sampled = span.sampled
            self._trace.append(span)
            span._context = self

    def close_span(self, span):
        """
        Mark a span as a finished, increasing the internal counter to prevent
        cycles inside _trace list.
        """
        with self._lock:
            self._finished_spans += 1
            self._current_span = span._parent

            # notify if the trace is not closed properly; this check is executed only
            # if the tracer debug_logging is enabled and when the root span is closed
            # for an unfinished trace. This logging is meant to be used for debugging
            # reasons, and it doesn't mean that the trace is wrongly generated.
            # In asynchronous environments, it's legit to close the root span before
            # some children. On the other hand, asynchronous web frameworks still expect
            # to close the root span after all the children.
            tracer = getattr(span, '_tracer', None)
            if tracer and tracer.debug_logging and span._parent is None and not self._is_finished():
                opened_spans = len(self._trace) - self._finished_spans
                log.debug('Root span "%s" closed, but the trace has %d unfinished spans:', span.name, opened_spans)
                spans = [x for x in self._trace if not x._finished]
                for wrong_span in spans:
                    log.debug('\n%s', wrong_span.pprint())

    def is_finished(self):
        """
        Returns if the trace for the current Context is finished or not. A Context
        is considered finished if all spans in this context are finished.
        """
        with self._lock:
            return self._is_finished()

    def is_sampled(self):
        """
        Returns if the ``Context`` contains sampled spans.
        """
        with self._lock:
            return self._sampled

    def get(self):
        """
        Returns a tuple containing the trace list generated in the current context and
        if the context is sampled or not. It returns (None, None) if the ``Context`` is
        not finished. If a trace is returned, the ``Context`` will be reset so that it
        can be re-used immediately.

        This operation is thread-safe.
        """
        with self._lock:
            if self._is_finished():
                trace = self._trace
                sampled = self._sampled
                # clean the current state
                self._trace = []
                self._sampled = False
                self._finished_spans = 0
                self._current_span = None
                return trace, sampled
            else:
                return None, None

    def _is_finished(self):
        """
        Internal method that checks if the ``Context`` is finished or not.
        """
        num_traces = len(self._trace)
        return num_traces > 0 and num_traces == self._finished_spans


class ThreadLocalContext(object):
    """
    ThreadLocalContext can be used as a tracer global reference to create
    a different ``Context`` for each thread. In synchronous tracer, this
    is required to prevent multiple threads sharing the same ``Context``
    in different executions.
    """
    def __init__(self):
        self._locals = threading.local()

    def set(self, ctx):
        setattr(self._locals, 'context', ctx)

    def get(self):
        ctx = getattr(self._locals, 'context', None)
        if not ctx:
            # create a new Context if it's not available; this action
            # is done once because the Context has the reset() method
            # to reuse the same instance
            ctx = Context()
            self._locals.context = ctx

        return ctx
