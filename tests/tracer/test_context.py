import pickle

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
        (Context(), Span("")),
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


def test_traceparent():
    def validate_traceparent(context, sampled_expected):
        version_hex, traceid_hex, spanid_hex, sampled_hex = context._traceparent.split("-")
        assert version_hex == "00"

        assert len(traceid_hex) == 32
        assert traceid_hex == "{:032x}".format(context.trace_id)

        assert len(spanid_hex) == 16
        assert spanid_hex == "{:016x}".format(context.span_id)

        assert len(sampled_hex) == 2
        assert sampled_hex == sampled_expected

    span = Span("span_a")
    span.context.sampling_priority = -1
    validate_traceparent(span.context, "00")

    span = Span("span_b")
    span.context.sampling_priority = 0
    validate_traceparent(span.context, "00")

    span = Span("span_c")
    span.context.sampling_priority = 1
    validate_traceparent(span.context, "01")


@pytest.mark.parametrize(
    "context",
    [
        Context(),
        Context(trace_id=123, span_id=321),
        Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2),
        Context(trace_id=123, span_id=321, meta={"meta": "value"}, metrics={"metric": 4.556}),
    ],
)
def test_context_serializable(context):
    # type: (Context) -> None
    state = pickle.dumps(context)
    restored = pickle.loads(state)
    assert context == restored
