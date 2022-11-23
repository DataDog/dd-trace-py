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


@pytest.mark.parametrize(
    "context,expected_traceparent",
    [
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=1,
                traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=0,
                traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=2,
                traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=-1,
                traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=-1,
                traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        (
            Context(trace_id=11803532876627986230, span_id=67667974448284343, sampling_priority=1),
            "00-0000000000000000a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        (
            Context(trace_id=123, span_id=123, sampling_priority=1),
            "00-0000000000000000000000000000007b-000000000000007b-01",
        ),
        (
            Context(trace_id=11803532876627986230, span_id=67667974448284343, sampling_priority=-1),
            "00-0000000000000000a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        (
            Context(trace_id=11803532876627986230, span_id=67667974448284343, sampling_priority=2),
            "00-0000000000000000a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        # (Context(trace_id=123, span_id=321)),
        # (Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2)),
        # (Context(trace_id=123, span_id=321, meta={"meta": "value"}, metrics={"metric": 4.556})),
    ],
)
def test_traceparent(context, expected_traceparent):
    # type: (Context,str) -> None
    assert context._traceparent == expected_traceparent


@pytest.mark.parametrize(
    "context,expected_tracestate",
    [
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=1,
                meta={"tracestate": "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE"},
            ),
            "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
        ),
        # (Context(trace_id=123, span_id=321)),
        # (Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2)),
        # (Context(trace_id=123, span_id=321, meta={"meta": "value"}, metrics={"metric": 4.556})),
    ],
)
def test_tracestate(context, expected_tracestate):
    # type: (Context,str) -> None
    assert context._tracestate == expected_tracestate
