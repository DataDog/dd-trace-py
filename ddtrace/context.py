from .internal.logger import get_logger

log = get_logger(__name__)


class Context(object):
    """Represents a snapshot of a trace to be used to propagate a trace
    across execution boundaries (eg. distributed tracing).

    A Context contains the span_id of the active span at the time the context
    is created.
    """

    __slots__ = [
        "trace_id",
        "span_id",
        "dd_origin",
        "sampling_priority",
    ]

    def __init__(self, trace_id=None, span_id=None, sampling_priority=None, dd_origin=None):
        # type (Optional[int], Optional[int], Optional[int], Optional[str]) -> None
        self.trace_id = trace_id
        self.span_id = span_id
        self.dd_origin = dd_origin
        self.sampling_priority = sampling_priority

    def __eq__(self, other):
        return (
            self.span_id == other.span_id
            and self.trace_id == other.trace_id
            and self.sampling_priority == other.sampling_priority
            and self.dd_origin == other.dd_origin
        )
