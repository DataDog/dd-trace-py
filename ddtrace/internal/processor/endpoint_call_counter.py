from dataclasses import dataclass
from dataclasses import field
import typing

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span  # noqa:F401
from ddtrace.ext import SpanTypes
from ddtrace.internal import forksafe
from ddtrace.internal.compat import ensure_text


EndpointCountsType = typing.Dict[str, int]


@dataclass(eq=False)
class EndpointCallCounterProcessor(SpanProcessor):
    endpoint_counts: EndpointCountsType = field(default_factory=dict, init=False, repr=False, compare=False)
    _endpoint_counts_lock: typing.ContextManager = field(
        default_factory=forksafe.Lock, init=False, repr=False, compare=False
    )
    _enabled: bool = field(default=False, repr=False, compare=False)

    def enable(self):
        # type: () -> None
        self._enabled = True

    def on_span_start(self, span):
        # type: (Span) -> None
        pass

    def on_span_finish(self, span):
        # type: (Span) -> None
        if not self._enabled:
            return
        if span._local_root == span and span.span_type == SpanTypes.WEB:
            resource = ensure_text(span.resource, errors="backslashreplace")
            with self._endpoint_counts_lock:
                self.endpoint_counts[resource] = self.endpoint_counts.get(resource, 0) + 1

    def reset(self):
        # type: () -> EndpointCountsType
        with self._endpoint_counts_lock:
            counts = self.endpoint_counts
            self.endpoint_counts = {}
            return counts
