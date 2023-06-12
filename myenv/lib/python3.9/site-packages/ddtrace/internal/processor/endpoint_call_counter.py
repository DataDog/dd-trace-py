import typing

import attr

from ddtrace.ext import SpanTypes
from ddtrace.internal import forksafe
from ddtrace.internal.compat import ensure_str
from ddtrace.internal.processor import SpanProcessor
from ddtrace.span import Span


EndpointCountsType = typing.Dict[str, int]


@attr.s(eq=False)
class EndpointCallCounterProcessor(SpanProcessor):

    endpoint_counts = attr.ib(init=False, repr=False, type=EndpointCountsType, factory=lambda: {}, eq=False)
    _endpoint_counts_lock = attr.ib(init=False, repr=False, factory=forksafe.Lock, eq=False)
    _enabled = attr.ib(default=False, repr=False, eq=False)

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
            resource = ensure_str(span.resource, errors="backslashreplace")
            with self._endpoint_counts_lock:
                self.endpoint_counts[resource] = self.endpoint_counts.get(resource, 0) + 1

    def reset(self):
        # type: () -> EndpointCountsType
        with self._endpoint_counts_lock:
            counts = self.endpoint_counts
            self.endpoint_counts = {}
            return counts
