import typing

import attr

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span  # noqa:F401
from ddtrace.ext import SpanTypes
from ddtrace.internal import forksafe
from ddtrace.internal.compat import ensure_text


EndpointCountsType = typing.Dict[str, int]


@attr.s(eq=False)
class EndpointCallCounterProcessor(SpanProcessor):
    endpoint_counts = attr.ib(init=False, repr=False, type=EndpointCountsType, factory=lambda: {}, eq=False)
    # Mapping from endpoint to list of span IDs, here we use mapping from
    # endpoint to span_ids instead of mapping from span_id to endpoint to
    # avoid creating a new string object for each span id.
    endpoint_to_span_ids = attr.ib(
        init=False, repr=False, type=typing.Dict[str, typing.List[int]], factory=dict, eq=False
    )
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
            resource = ensure_text(span.resource, errors="backslashreplace")
            span_id = span.span_id
            with self._endpoint_counts_lock:
                self.endpoint_counts[resource] = self.endpoint_counts.get(resource, 0) + 1
                if resource not in self.endpoint_to_span_ids:
                    self.endpoint_to_span_ids[resource] = []
                self.endpoint_to_span_ids[resource].append(span_id)

    def reset(self) -> typing.Tuple[EndpointCountsType, typing.Dict[str, typing.List[int]]]:
        with self._endpoint_counts_lock:
            counts = self.endpoint_counts
            self.endpoint_counts = {}
            span_ids = self.endpoint_to_span_ids
            self.endpoint_to_span_ids = {}
            return counts, span_ids
