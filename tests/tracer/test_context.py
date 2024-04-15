# -*- coding: utf-8 -*-
import pickle
from typing import Optional  # noqa:F401

import pytest

from ddtrace._trace._span_link import SpanLink
from ddtrace._trace.context import Context
from ddtrace._trace.span import Span


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


def test_traceparent_basic():
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
        Context(trace_id=123, span_id=321, is_remote=True),
        Context(trace_id=123, span_id=321, dd_origin="synthetics", sampling_priority=2, is_remote=False),
        Context(trace_id=123, span_id=321, meta={"meta": "value"}, metrics={"metric": 4.556}),
        Context(
            trace_id=123,
            span_id=321,
            meta={"meta": "value"},
            metrics={"metric": 4.556},
            span_links=[
                SpanLink(
                    trace_id=123 << 64,
                    span_id=456,
                    tracestate="congo=t61rcWkgMzE",
                    flags=1,
                    attributes={"link.name": "some_name"},
                )
            ],
        ),
        Context(
            trace_id=123, span_id=321, meta={"meta": "value"}, metrics={"metric": 4.556}, baggage={"some_value": 1}
        ),
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
                meta={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=0,
                meta={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=2,
                meta={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"},
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=-1,
                meta={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
            ),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=-1,
                meta={"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"},
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
        (
            Context(
                span_id=67667974448284343,
                sampling_priority=1,
            ),
            "",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                sampling_priority=1,
            ),
            "",
        ),
    ],
    ids=[
        "basic_tp",
        "sampling_priority_0_on_context_1_on_tp",
        "sampling_priority_2_on_context_0_on_tp",
        "sampling_priority_-1_on_context_1_on_tp",
        "no_tp_in_context",
        "sampling_priority_-1_on_context_0_on_tp",
        "shortened_trace_and_span_id",
        "no_tp_in_context_sampling_priority_-1",
        "no_tp_in_context_sampling_priority_2",
        "no_trace_id_or_tp",
        "no_span_id_or_tp",
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
                meta={
                    "tracestate": "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                dd_origin="rum",
            ),
            "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=1,
                dd_origin="rum",
                meta={"tracestate": "congo=t61rcWkgMzE"},
            ),
            "dd=s:1;o:rum,congo=t61rcWkgMzE",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=2,
                meta={
                    "tracestate": "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE,nr=ok,s=ink",
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                dd_origin="synthetics",
            ),
            "dd=s:2;o:synthetics;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE,nr=ok,s=ink",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=-1,
                meta={
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                dd_origin="synthetics",
            ),
            "dd=s:-1;o:synthetics;t.dm:-4;t.usr.id:baz64",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=1,
                meta={
                    "tracestate": "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                    "_dd.p.unknown": "unk",
                },
                dd_origin="rum",
            ),
            "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64;t.unknown:unk,congo=t61rcWkgMzE",
        ),
        (
            Context(),
            "",
        ),
        (  # for value replace ",", ";" and characters outside the ASCII range 0x20 to 0x7E with _
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=1,
                meta={
                    "tracestate": "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64",
                    "_dd.p.dm": ";5;",
                    "_dd.p.usr.id": "b,z64,",
                    "_dd.p.unk": ";2",
                },
                dd_origin="rum",
            ),
            "dd=s:1;o:rum;t.dm:_5_;t.usr.id:b_z64_;t.unk:_2",
        ),
        (  # for key replace ",", "=", and characters outside the ASCII range 0x20 to 0x7E with _
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=1,
                meta={
                    "tracestate": "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64",
                    "_dd.p.dm": "5",
                    "_dd.p.usr.id": "bz64",
                    "_dd.p.unk¢": "2",
                },
                dd_origin="rum",
            ),
            "dd=s:1;o:rum;t.dm:5;t.usr.id:bz64;t.unk_:2",
        ),
        (
            Context(
                trace_id=11803532876627986230,
                span_id=67667974448284343,
                sampling_priority=1,
                meta={
                    "tracestate": "dd=s:1;o:rum;t.dm:-4;t.usr.id:baz64",
                },
                dd_origin=";r,um=",
            ),
            # = is encoded as ~
            "dd=s:1;o:_r_um~",
        ),
    ],
    ids=[
        "basic_ts_with_extra_listmember",
        "no_dd_list_member_in_meta_ts",
        "multiple_additional_list_members_and_sampling_priority_override",
        "negative sampling_priority",
        "propagate_unknown_dd.p_values",
        "no values",
        "equals_and_comma_chars_replaced",
        "key_outside_range_replaced_w_underscore",
        "test_origin_specific_replacement",
    ],
)
def test_tracestate(context, expected_tracestate):
    # type: (Context,str) -> None
    assert context._tracestate == expected_tracestate


@pytest.mark.parametrize(
    "ctx,expected_dd_origin",
    [
        (Context(), None),
        (Context(dd_origin="abcABC"), "abcABC"),
        (Context(dd_origin="abcABC123"), "abcABC123"),
        (Context(dd_origin="!@#$%^&*()>?"), "!@#$%^&*()>?"),
        (Context(dd_origin="\x00\x10"), None),
        (Context(dd_origin="\x80"), None),
        (Context(dd_origin="§¢À"), None),
    ],
)
def test_dd_origin_character_set(ctx, expected_dd_origin):
    # type: (Context,Optional[str]) -> None
    assert ctx.dd_origin == expected_dd_origin


def test_is_remote():
    # type: () -> None
    """Ensure that the is_remote flag is set to False on all local spans"""
    # Context._is_remote should be True by default
    ctx = Context(trace_id=123, span_id=321)
    assert ctx._is_remote is True

    # Span.context.is_remote should ALWAYS evaluate to False.
    local_span = Span("span_with_context", context=ctx)
    local_span.context.trace_id = 123
    local_span.context.span_id = 321
    local_span.context._is_remote = False

    # is_remote should be set to False on root spans.
    root = Span("root")
    assert root.context._is_remote is False
