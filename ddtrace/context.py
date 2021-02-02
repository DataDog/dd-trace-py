from .internal.logger import get_logger

log = get_logger(__name__)


class Context(object):
    """Represents a remote trace and active span in order to perform distributed
    tracing.
    """

    __slots__ = [
        "trace_id",
        "span_id",
        "_sampling_priority",
        "_dd_origin",
    ]

    def __init__(self, trace_id=None, span_id=None, sampling_priority=None, _dd_origin=None):
        # type (Optional[int], Optional[int], Optional[int], Optional[str]) -> None
        self.trace_id = trace_id
        self.span_id = span_id
        self._sampling_priority = sampling_priority
        self._dd_origin = _dd_origin

    def __eq__(self, other):
        return (
            self.span_id == other.span_id
            and self.trace_id == other.trace_id
            and self.sampling_priority == other.sampling_priority
            and self._dd_origin == other._dd_origin
        )

    @property
    def sampling_priority(self):
        return self._sampling_priority

    @sampling_priority.setter
    def sampling_priority(self, value):
        self._sampling_priority = value
        from ddtrace.tracer import _get_or_create_trace

        trace = _get_or_create_trace(self.trace_id)
        if trace:
            trace.sampling_priority = self._sampling_priority
