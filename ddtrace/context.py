import threading
from typing import Optional
from typing import TYPE_CHECKING

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .internal.logger import get_logger
from .utils.deprecation import deprecated
from .utils.formats import asbool
from .utils.formats import get_env


if TYPE_CHECKING:
    from ddtrace import Span

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
    _partial_flush_min_spans = int(get_env("tracer", "partial_flush_min_spans", default=500))  # type: ignore[arg-type]

    def __init__(
        self,
        trace_id=None,  # type: Optional[int]
        span_id=None,  # type: Optional[int]
        sampling_priority=None,  # type: Optional[int]
        dd_origin=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        """
        Initialize a new thread-safe ``Context``.

        :param int trace_id: trace_id of parent span
        :param int span_id: span_id of parent span
        """
        self._current_span = None  # type: Optional[Span]
        self._lock = threading.RLock()

        self._parent_trace_id = trace_id
        self._parent_span_id = span_id
        self._sampling_priority = sampling_priority
        self._local_root_span = None  # type: Optional[Span]
        self.dd_origin = dd_origin

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
        # type: (int) -> None
        """Set sampling priority."""
        with self._lock:
            self._sampling_priority = value

    def _clone(self):
        # type: () -> Context
        with self._lock:
            new_ctx = Context(
                trace_id=self._parent_trace_id,
                span_id=self._parent_span_id,
                sampling_priority=self._sampling_priority,
            )
            new_ctx._current_span = self._current_span
            return new_ctx

    @deprecated("Cloning contexts will no longer be required in 0.50", version="0.50")
    def clone(self):
        # type: () -> Context
        """
        Partially clones the current context.
        It copies everything EXCEPT the registered and finished spans.
        """
        return self._clone()

    def _get_current_root_span(self):
        # type: () -> Optional[Span]
        with self._lock:
            return self._local_root_span

    @deprecated("Please use tracer.current_root_span() instead", version="0.50")
    def get_current_root_span(self):
        # type: () -> Optional[Span]
        """
        Return the root span of the context or None if it does not exist.
        """
        return self._get_current_root_span()

    def _get_current_span(self):
        # type: () -> Optional[Span]
        with self._lock:
            return self._current_span

    @deprecated("Please use tracer.current_span() instead", version="0.50")
    def get_current_span(self):
        # type: () -> Optional[Span]
        """
        Return the last active span that corresponds to the last inserted
        item in the trace list. This cannot be considered as the current active
        span in asynchronous environments, because some spans can be closed
        earlier while child spans still need to finish their traced execution.
        """
        return self._get_current_span()

    def _set_current_span(self, span):
        # type: (Optional[Span]) -> None
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

    def _add_span(self, span):
        # type: (Span) -> None
        with self._lock:
            # Assume the first span added to the context is the local root
            if self._local_root_span is None:
                self._local_root_span = span
            self._set_current_span(span)
            span._context = self

    @deprecated("Context will no longer support active span management in a later version.", version="0.50")
    def add_span(self, span):
        # type: (Span) -> None
        """Activates span in the context."""
        return self._add_span(span)

    def _close_span(self, span):
        # type: (Span) -> None
        with self._lock:
            if span == self._local_root_span:
                if self.dd_origin is not None:
                    span.meta[ORIGIN_KEY] = self.dd_origin
                if self._sampling_priority is not None:
                    span.metrics[SAMPLING_PRIORITY_KEY] = self._sampling_priority

            # If a parent exists to the closing span and it is unfinished, then
            # activate it next.
            if span._parent and not span._parent.finished:
                self._set_current_span(span._parent)
            # Else if the span is the local root of this context, then clear the
            # context so the next trace can be started.
            elif span == self._local_root_span:
                self._set_current_span(span._parent)
                self._local_root_span = None
                self._parent_trace_id = None
                self._parent_span_id = None
                self._sampling_priority = None
            # Else the span that is closing is closing after its parent.
            # This is most likely an error. To ensure future traces are not
            # affected clear out the context and set the current span to
            # ``None``.
            else:
                log.debug(
                    "span %r closing after its parent %r, this is an error when not using async", span, span._parent
                )
                self._set_current_span(None)
                self._local_root_span = None
                self._parent_trace_id = None
                self._parent_span_id = None
                self._sampling_priority = None

    @deprecated(message="Context will no longer support active span management in a later version.", version="0.50")
    def close_span(self, span):
        # type: (Span) -> None
        """Updates the context after a span has finished.

        The next active span becomes `span`'s parent.
        """
        return self._close_span(span)
