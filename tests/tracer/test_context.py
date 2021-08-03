import pytest

from ddtrace.context import Context
from ddtrace.span import Span


@pytest.mark.parametrize(
    "ctx1,ctx2",
    [
        (Context(), Context()),
        (Context(trace_id=123), Context(trace_id=123)),
        (
            Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2),
            Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2),
        ),
    ],
)
def test_eq(ctx1, ctx2):
    assert ctx1 == ctx2


@pytest.mark.parametrize(
    "ctx1,ctx2",
    [
        (Context(), Span(None, "")),
        (Context(), None),
        (Context(), object()),
        (None, Context()),
        (Context(), 5),
        (5, Context()),
        (
            Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2),
            Context(trace_id=1234, span_id=321, dd_origin="synthetics", sampling_priority=2),
        ),
        (
            Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2),
            Context(trace_id=123, span_id=3210, dd_origin="synthetics", sampling_priority=2),
        ),
        (
            Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2),
            Context(trace_id=123, span_id=321, dd_origin="synthetics1", sampling_priority=2),
        ),
        (
            Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2),
            Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=0),
        ),
    ],
)
def test_not_eq(ctx1, ctx2):
    assert ctx1 != ctx2
