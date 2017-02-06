import threading


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
            self._trace.append(span)

    def finish_span(self, span):
        """
        Mark a span as a finished, increasing the internal counter to prevent
        cycles inside _trace list.
        """
        with self._lock:
            self._finished_spans += 1
            self._current_span = span._parent

    def get_current_trace(self):
        """
        Returns the trace list generated in the current context.
        """
        with self._lock:
            return self._trace

    def is_finished(self):
        """
        Returns if the trace for the current Context is finished or not. A Context
        is considered finished if all spans in this context are finished.
        """
        with self._lock:
            return len(self._trace) == self._finished_spans

    def reset(self):
        """
        Reset the current Context so that it is re-usable.
        """
        with self._lock:
            self._trace = []
            self._finished_spans = 0
            self._current_span = None


class ThreadLocalContext(object):
    """
    ThreadLocalContext can be used as a tracer global reference to create
    a different ``Context`` for each thread. In synchronous tracer, this
    is required to prevent multiple threads sharing the same ``Context``
    in different executions.
    """
    def __init__(self):
        self._locals = threading.local()

    def get(self):
        ctx = getattr(self._locals, 'context', None)
        if not ctx:
            # create a new Context if it's not available; this action
            # is done once because the Context has the reset() method
            # to reuse the same instance
            ctx = Context()
            self._locals.context = ctx

        return ctx
