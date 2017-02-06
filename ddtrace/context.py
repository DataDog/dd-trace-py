import logging
import threading


log = logging.getLogger(__name__)


class Context(object):
    """
    Context is used to keep track of a hierarchy of spans for the current
    execution flow.

    TODO: asyncio is not thread-safe by default. The fact that this class is
    thread-safe is an implementation detail. Avoid mutex usage when the Context
    is used in async code.

    TODO: In synchronous environment each thread has its own copy of the global
    Context through the ThreadLocalContext class (compliant with the original
    implementation). This works for synchronous code, but in some environments
    it may not work (i.e. gevent?).
    """
    def __init__(self):
        """
        Initialize a new Context.
        """
        self._trace = []
        self._finished_spans = 0
        # TODO: may be replaced by the tail of the list? may be not "internal"?
        self._current_span = None
        self._lock = threading.Lock()

    def get_current_span(self):
        """
        TODO: check if getters are needed to be generic in async code
        Return the last active span. This call makes sense only on synchronous code.
        """
        with self._lock:
            return self._current_span

    def set_current_span(self, span):
        """
        TODO: check if setters are needed to be generic in async code
        Set the last active span. This call makes sense only on synchronous code.
        """
        with self._lock:
            self._current_span = span

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
        TODO: _trace is mutable so this is dangerous. Keep track of closed spans in an int.
        Returns the current context trace list.
        """
        with self._lock:
            return self._trace

    def is_finished(self):
        """
        TODO this method may become an helper; check in the case of AsyncContext if the
        separation design is correct.
        Returns if the trace for the current Context is finished.
        """
        with self._lock:
            return len(self._trace) == self._finished_spans

    def reset(self):
        """
        TODO: check for AsyncContext
        Reset the current Context if it should be re-usable.
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
