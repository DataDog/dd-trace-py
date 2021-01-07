from .internal.logger import get_logger

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

    def __init__(self, trace_id=None, span_id=None, sampling_priority=None, _dd_origin=None):
        """
        Initialize a new thread-safe ``Context``.

        :param int trace_id: trace_id of parent span
        :param int span_id: span_id of parent span
        """
        self._finished_spans = 0
        self._current_span = None

        self._parent_trace_id = trace_id
        self._parent_span_id = span_id
        self._sampling_priority = sampling_priority
        self._dd_origin = _dd_origin

    @property
    def trace_id(self):
        """Return current context trace_id."""
        return self._parent_trace_id

    @property
    def span_id(self):
        """Return current context span_id."""
        return self._parent_span_id

    @property
    def sampling_priority(self):
        """Return current context sampling priority."""
        return self._sampling_priority

    @sampling_priority.setter
    def sampling_priority(self, value):
        """Set sampling priority."""
        self._sampling_priority = value

    def __eq__(self, other):
        return (
            self.span_id == other.span_id
            and self.trace_id == other.trace_id
            and self.sampling_priority == other.sampling_priority
            and self._dd_origin == other._dd_origin
        )

    def clone(self):
        """
        Partially clones the current context.
        It copies everything EXCEPT the registered and finished spans.
        """
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
        return None

    def get_current_span(self):
        """
        Return the last active span that corresponds to the last inserted
        item in the trace list. This cannot be considered as the current active
        span in asynchronous environments, because some spans can be closed
        earlier while child spans still need to finish their traced execution.
        """
        return None

    def add_span(self, span):
        """
        Add a span to the context trace list, keeping it as the last active span.
        """
        pass

    def close_span(self, span):
        """
        Mark a span as a finished, increasing the internal counter to prevent
        cycles inside _trace list.
        """
        pass
