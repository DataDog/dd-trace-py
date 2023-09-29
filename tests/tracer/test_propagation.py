# -*- coding: utf-8 -*-
import json
import logging
import os

import pytest

from ddtrace.context import Context
from ddtrace.internal.constants import PROPAGATION_STYLE_B3_MULTI
from ddtrace.internal.constants import PROPAGATION_STYLE_B3_SINGLE
from ddtrace.internal.constants import PROPAGATION_STYLE_DATADOG
from ddtrace.internal.constants import _PROPAGATION_STYLE_NONE
from ddtrace.internal.constants import _PROPAGATION_STYLE_W3C_TRACECONTEXT
from ddtrace.propagation._utils import get_wsgi_header
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.http import HTTP_HEADER_ORIGIN
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_SAMPLING_PRIORITY
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from ddtrace.propagation.http import _HTTP_HEADER_B3_FLAGS
from ddtrace.propagation.http import _HTTP_HEADER_B3_SAMPLED
from ddtrace.propagation.http import _HTTP_HEADER_B3_SINGLE
from ddtrace.propagation.http import _HTTP_HEADER_B3_SPAN_ID
from ddtrace.propagation.http import _HTTP_HEADER_B3_TRACE_ID
from ddtrace.propagation.http import _HTTP_HEADER_TAGS
from ddtrace.propagation.http import _HTTP_HEADER_TRACEPARENT
from ddtrace.propagation.http import _HTTP_HEADER_TRACESTATE
from ddtrace.propagation.http import _TraceContext

from ..utils import override_global_config


NOT_SET = object()


def test_inject(tracer):
    meta = {"_dd.p.test": "value", "_dd.p.other": "value", "something": "value"}
    ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
    tracer.context_provider.activate(ctx)
    with tracer.trace("global_root_span") as span:
        headers = {}
        HTTPPropagator.inject(span.context, headers)

        assert int(headers[HTTP_HEADER_TRACE_ID]) == span.trace_id
        assert int(headers[HTTP_HEADER_PARENT_ID]) == span.span_id
        assert int(headers[HTTP_HEADER_SAMPLING_PRIORITY]) == span.context.sampling_priority
        assert headers[HTTP_HEADER_ORIGIN] == span.context.dd_origin
        # The ordering is non-deterministic, so compare as a list of tags
        tags = set(headers[_HTTP_HEADER_TAGS].split(","))
        assert tags == set(["_dd.p.test=value", "_dd.p.other=value"])


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=PROPAGATION_STYLE_DATADOG),
)
def test_inject_128bit_trace_id_datadog():
    from ddtrace.context import Context
    from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        # Get the hex representation of the 64 most signicant bits
        trace_id_hob_hex = "{:032x}".format(trace_id)[:16]
        ctx = Context(trace_id=trace_id, meta={"_dd.t.tid": trace_id_hob_hex})
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            assert span.trace_id == trace_id
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            assert headers == {
                "x-datadog-trace-id": str(span._trace_id_64bits),
                "x-datadog-parent-id": str(span.span_id),
                "x-datadog-tags": "=".join([HIGHER_ORDER_TRACE_ID_BITS, trace_id_hob_hex]),
            }


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=PROPAGATION_STYLE_B3_MULTI),
)
def test_inject_128bit_trace_id_b3multi():
    from ddtrace.context import Context
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        ctx = Context(trace_id=trace_id)
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            assert span.trace_id == trace_id
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            trace_id_hex = "{:032x}".format(span.trace_id)
            span_id_hex = "{:016x}".format(span.span_id)
            assert headers == {"x-b3-traceid": trace_id_hex, "x-b3-spanid": span_id_hex}


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=PROPAGATION_STYLE_B3_SINGLE),
)
def test_inject_128bit_trace_id_b3_single_header():
    from ddtrace.context import Context
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        ctx = Context(trace_id=trace_id)
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            assert span.trace_id == trace_id
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            trace_id_hex = "{:032x}".format(span.trace_id)
            span_id_hex = "{:016x}".format(span.span_id)
            assert headers == {"b3": "%s-%s" % (trace_id_hex, span_id_hex)}


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=_PROPAGATION_STYLE_W3C_TRACECONTEXT),
)
def test_inject_128bit_trace_id_tracecontext():
    from ddtrace.context import Context
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        ctx = Context(trace_id=trace_id)
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            assert span.trace_id == trace_id
            headers = {}
            HTTPPropagator.inject(span.context, headers)
            trace_id_hex = "{:032x}".format(span.trace_id)
            span_id_hex = "{:016x}".format(span.span_id)
            assert headers == {"traceparent": "00-%s-%s-00" % (trace_id_hex, span_id_hex)}


def test_inject_tags_unicode(tracer):
    """We properly encode when the meta key as long as it is just ascii characters"""
    # Context._meta allows str and bytes for keys
    meta = {u"_dd.p.test": u"unicode"}
    ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
    tracer.context_provider.activate(ctx)
    with tracer.trace("global_root_span") as span:
        headers = {}
        HTTPPropagator.inject(span.context, headers)

        # The ordering is non-deterministic, so compare as a list of tags
        tags = set(headers[_HTTP_HEADER_TAGS].split(","))
        assert tags == set(["_dd.p.test=unicode"])


def test_inject_tags_bytes(tracer):
    """We properly encode when the meta key as long as it is just ascii characters"""
    # Context._meta allows str and bytes for keys
    # FIXME: W3C does not support byte headers
    overrides = {
        "_propagation_style_extract": [PROPAGATION_STYLE_DATADOG],
        "_propagation_style_inject": [PROPAGATION_STYLE_DATADOG],
    }
    with override_global_config(overrides):
        meta = {u"_dd.p.test": b"bytes"}
        ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            headers = {}
            HTTPPropagator.inject(span.context, headers)

            # The ordering is non-deterministic, so compare as a list of tags
            tags = set(headers[_HTTP_HEADER_TAGS].split(","))
            assert tags == set(["_dd.p.test=bytes"])


def test_inject_tags_unicode_error(tracer):
    """Unicode characters are not allowed"""
    meta = {u"_dd.p.test": u"unicode value ☺️"}
    ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
    tracer.context_provider.activate(ctx)
    with tracer.trace("global_root_span") as span:
        headers = {}
        HTTPPropagator.inject(span.context, headers)

        assert _HTTP_HEADER_TAGS not in headers
        assert ctx._meta["_dd.propagation_error"] == "encoding_error"


def test_inject_tags_large(tracer):
    """When we have a single large tag that won't fit"""
    # DEV: Limit is 512 for x-datadog-tags
    meta = {"_dd.p.dm": ("x" * 512)[len("_dd.p.dm") - 1 :]}
    ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
    tracer.context_provider.activate(ctx)
    with tracer.trace("global_root_span") as span:
        headers = {}
        HTTPPropagator.inject(span.context, headers)

        assert _HTTP_HEADER_TAGS not in headers
        assert ctx._meta["_dd.propagation_error"] == "inject_max_size"


def test_inject_tags_invalid(tracer):
    # DEV: "=" and "," are not allowed in keys or values
    meta = {"_dd.p.test": ",value="}
    ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
    tracer.context_provider.activate(ctx)
    with tracer.trace("global_root_span") as span:
        headers = {}
        HTTPPropagator.inject(span.context, headers)

        assert _HTTP_HEADER_TAGS not in headers
        assert ctx._meta["_dd.propagation_error"] == "encoding_error"


def test_inject_tags_disabled(tracer):
    with override_global_config(dict(_x_datadog_tags_enabled=False)):
        meta = {"_dd.p.test": "value"}
        ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            headers = {}
            HTTPPropagator.inject(span.context, headers)

            assert ctx._meta["_dd.propagation_error"] == "disabled"
            assert _HTTP_HEADER_TAGS not in headers


def test_inject_tags_previous_error(tracer):
    """When we have previously gotten an error, do not try to propagate tags"""
    # This value is valid
    meta = {"_dd.p.test": "value", "_dd.propagation_error": "some fake test value"}
    ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
    tracer.context_provider.activate(ctx)
    with tracer.trace("global_root_span") as span:
        headers = {}
        HTTPPropagator.inject(span.context, headers)

        assert _HTTP_HEADER_TAGS not in headers


def test_extract(tracer):
    headers = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
        "x-datadog-origin": "synthetics",
        "x-datadog-tags": "_dd.p.test=value,any=tag",
    }

    context = HTTPPropagator.extract(headers)

    tracer.context_provider.activate(context)

    with tracer.trace("local_root_span") as span:
        assert span.trace_id == 1234
        assert span.parent_id == 5678
        assert span.context.sampling_priority == 1
        assert span.context.dd_origin == "synthetics"
        assert span.context._meta == {
            "_dd.origin": "synthetics",
            "_dd.p.test": "value",
        }
        with tracer.trace("child_span") as child_span:
            assert child_span.trace_id == 1234
            assert child_span.parent_id != 5678
            assert child_span.context.sampling_priority == 1
            assert child_span.context.dd_origin == "synthetics"
            assert child_span.context._meta == {
                "_dd.origin": "synthetics",
                "_dd.p.test": "value",
            }


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=PROPAGATION_STYLE_DATADOG),
)
def test_extract_128bit_trace_ids_datadog():
    from ddtrace.internal.constants import HIGHER_ORDER_TRACE_ID_BITS
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        trace_id_hex = "{:032x}".format(trace_id)
        span_id = 1
        # Get the hex representation of the 64 most signicant bits
        trace_id_64bit = trace_id & 2 ** 64 - 1
        headers = {
            "x-datadog-trace-id": str(trace_id_64bit),
            "x-datadog-parent-id": str(span_id),
            "x-datadog-tags": "=".join([HIGHER_ORDER_TRACE_ID_BITS, trace_id_hex[:16]]),
        }

        context = HTTPPropagator.extract(headers)
        tracer.context_provider.activate(context)
        with tracer.trace("local_root_span") as span:
            assert span.trace_id == trace_id
            assert span.parent_id == span_id
            assert HIGHER_ORDER_TRACE_ID_BITS not in span.context._meta
            with tracer.trace("child_span") as child_span:
                assert child_span.trace_id == trace_id


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=PROPAGATION_STYLE_B3_MULTI),
)
def test_extract_128bit_trace_ids_b3multi():
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        trace_id_hex = "{:032x}".format(trace_id)
        span_id = 1
        span_id_hex = "{:016x}".format(span_id)
        headers = {
            "x-b3-traceid": trace_id_hex,
            "x-b3-spanid": span_id_hex,
        }

        context = HTTPPropagator.extract(headers)
        tracer.context_provider.activate(context)
        with tracer.trace("local_root_span") as span:
            assert span.trace_id == trace_id
            assert span.parent_id == span_id
            with tracer.trace("child_span") as child_span:
                assert child_span.trace_id == trace_id


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=PROPAGATION_STYLE_B3_SINGLE),
)
def test_extract_128bit_trace_ids_b3_single_header():
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        trace_id_hex = "{:032x}".format(trace_id)
        span_id = 1
        span_id_hex = "{:016x}".format(span_id)
        headers = {
            "b3": "%s-%s-1" % (trace_id_hex, span_id_hex),
        }

        context = HTTPPropagator.extract(headers)
        tracer.context_provider.activate(context)
        with tracer.trace("local_root_span") as span:
            assert span.trace_id == trace_id
            assert span.parent_id == span_id
            with tracer.trace("child_span") as child_span:
                assert child_span.trace_id == trace_id


@pytest.mark.subprocess(
    env=dict(DD_TRACE_PROPAGATION_STYLE=_PROPAGATION_STYLE_W3C_TRACECONTEXT),
)
def test_extract_128bit_trace_ids_tracecontext():
    from ddtrace.propagation.http import HTTPPropagator
    from tests.utils import DummyTracer

    tracer = DummyTracer()

    for trace_id in [2 ** 128 - 1, 2 ** 127 + 1, 2 ** 65 - 1, 2 ** 64 + 1, 2 ** 127 + 2 ** 63]:
        trace_id_hex = "{:032x}".format(trace_id)
        span_id = 1
        span_id_hex = "{:016x}".format(span_id)
        headers = {
            "traceparent": "00-%s-%s-01" % (trace_id_hex, span_id_hex),
        }

        context = HTTPPropagator.extract(headers)
        tracer.context_provider.activate(context)
        with tracer.trace("local_root_span") as span:
            assert span.trace_id == trace_id
            assert span.parent_id == span_id
            with tracer.trace("child_span") as child_span:
                assert child_span.trace_id == trace_id


def test_extract_unicode(tracer):
    """
    When input data is unicode
      we decode everything to str for Python2


    Cython encoder expects `context.dd_origin` to be `str`
    which for Python2 means only `str`, and `unicode` is
    not accepted. If `context.dd_origin` is `unicode`
    then trace encoding will fail and the trace will be
    lost.
    """
    headers = {
        u"x-datadog-trace-id": u"1234",
        u"x-datadog-parent-id": u"5678",
        u"x-datadog-sampling-priority": u"1",
        u"x-datadog-origin": u"synthetics",
        u"x-datadog-tags": u"_dd.p.test=value,any=tag",
    }

    context = HTTPPropagator.extract(headers)

    tracer.context_provider.activate(context)

    with tracer.trace("local_root_span") as span:
        assert span.trace_id == 1234
        assert span.parent_id == 5678
        assert span.context.sampling_priority == 1

        assert span.context.dd_origin == "synthetics"
        assert type(span.context.dd_origin) is str

        assert span.context._meta == {
            "_dd.origin": "synthetics",
            "_dd.p.test": "value",
        }
        with tracer.trace("child_span") as child_span:
            assert child_span.trace_id == 1234
            assert child_span.parent_id != 5678
            assert child_span.context.sampling_priority == 1
            assert child_span.context.dd_origin == "synthetics"
            assert child_span.context._meta == {
                "_dd.origin": "synthetics",
                "_dd.p.test": "value",
            }


@pytest.mark.parametrize(
    "x_datadog_tags, expected_trace_tags",
    [
        ("_dd.p.dm=-0", {"_dd.p.dm": "-0"}),
        ("_dd.p.dm=-0", {"_dd.p.dm": "-0"}),
        ("_dd.p.dm=-", {"_dd.propagation_error": "decoding_error"}),
        ("_dd.p.dm=--1", {"_dd.propagation_error": "decoding_error"}),
        ("_dd.p.dm=-1.0", {"_dd.propagation_error": "decoding_error"}),
        ("_dd.p.dm=-10", {"_dd.propagation_error": "decoding_error"}),
    ],
)
def test_extract_dm(x_datadog_tags, expected_trace_tags):
    headers = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
        "x-datadog-origin": "synthetics",
        "x-datadog-tags": x_datadog_tags,
    }

    context = HTTPPropagator.extract(headers)

    expected = {"_dd.origin": "synthetics"}
    expected.update(expected_trace_tags)

    assert context._meta == expected


def test_WSGI_extract(tracer):
    """Ensure we support the WSGI formatted headers as well."""
    headers = {
        "HTTP_X_DATADOG_TRACE_ID": "1234",
        "HTTP_X_DATADOG_PARENT_ID": "5678",
        "HTTP_X_DATADOG_SAMPLING_PRIORITY": "1",
        "HTTP_X_DATADOG_ORIGIN": "synthetics",
        "HTTP_X_DATADOG_TAGS": "_dd.p.test=value,any=tag",
    }

    context = HTTPPropagator.extract(headers)
    tracer.context_provider.activate(context)

    with tracer.trace("local_root_span") as span:
        assert span.trace_id == 1234
        assert span.parent_id == 5678
        assert span.context.sampling_priority == 1
        assert span.context.dd_origin == "synthetics"
        assert span.context._meta == {
            "_dd.origin": "synthetics",
            "_dd.p.test": "value",
        }


def test_extract_invalid_tags(tracer):
    # Malformed tags do not fail to extract the rest of the context
    headers = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
        "x-datadog-origin": "synthetics",
        "x-datadog-tags": "malformed=,=tags,",
    }

    context = HTTPPropagator.extract(headers)
    tracer.context_provider.activate(context)

    with tracer.trace("local_root_span") as span:
        assert span.trace_id == 1234
        assert span.parent_id == 5678
        assert span.context.sampling_priority == 1
        assert span.context.dd_origin == "synthetics"
        assert span.context._meta == {
            "_dd.origin": "synthetics",
            "_dd.propagation_error": "decoding_error",
        }


def test_extract_tags_large(tracer):
    """When we have a tagset larger than the extract limit"""
    # DEV: Limit is 512
    headers = {
        "x-datadog-trace-id": "1234",
        "x-datadog-parent-id": "5678",
        "x-datadog-sampling-priority": "1",
        "x-datadog-origin": "synthetics",
        "x-datadog-tags": "key=" + ("x" * (512 - len("key=") + 1)),
    }
    context = HTTPPropagator.extract(headers)
    tracer.context_provider.activate(context)

    with tracer.trace("local_root_span") as span:
        assert span.trace_id == 1234
        assert span.parent_id == 5678
        assert span.context.sampling_priority == 1
        assert span.context.dd_origin == "synthetics"
        assert span.context._meta == {
            "_dd.origin": "synthetics",
            "_dd.propagation_error": "extract_max_size",
        }


@pytest.mark.parametrize("trace_id", ["one", None, "123.4", "", NOT_SET])
# DEV: 10 is valid for parent id but is ignored if trace id is ever invalid
@pytest.mark.parametrize("parent_span_id", ["one", None, "123.4", "10", "", NOT_SET])
@pytest.mark.parametrize("sampling_priority", ["one", None, "123.4", "", NOT_SET])
@pytest.mark.parametrize("dd_origin", [None, NOT_SET])
# DEV: We have exhaustive tests in test_tagset for this parsing
@pytest.mark.parametrize("dd_tags", [None, "", "key=", "key=value,unknown=", NOT_SET])
def test_extract_bad_values(trace_id, parent_span_id, sampling_priority, dd_origin, dd_tags):
    headers = dict()
    wsgi_headers = dict()

    if trace_id is not NOT_SET:
        headers[HTTP_HEADER_TRACE_ID] = trace_id
        wsgi_headers[get_wsgi_header(HTTP_HEADER_TRACE_ID)] = trace_id
    if parent_span_id is not NOT_SET:
        headers[HTTP_HEADER_PARENT_ID] = parent_span_id
        wsgi_headers[get_wsgi_header(HTTP_HEADER_PARENT_ID)] = parent_span_id
    if sampling_priority is not NOT_SET:
        headers[HTTP_HEADER_SAMPLING_PRIORITY] = sampling_priority
        wsgi_headers[get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY)] = sampling_priority
    if dd_origin is not NOT_SET:
        headers[HTTP_HEADER_ORIGIN] = dd_origin
        wsgi_headers[get_wsgi_header(HTTP_HEADER_ORIGIN)] = dd_origin
    if dd_tags is not NOT_SET:
        headers[_HTTP_HEADER_TAGS] = dd_tags
        wsgi_headers[get_wsgi_header(_HTTP_HEADER_TAGS)] = dd_tags

    # x-datadog-*headers
    context = HTTPPropagator.extract(headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None
    assert context.dd_origin is None
    assert context._meta == {}

    # HTTP_X_DATADOG_* headers
    context = HTTPPropagator.extract(wsgi_headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None
    assert context.dd_origin is None
    assert context._meta == {}


def test_get_wsgi_header(tracer):
    assert get_wsgi_header("x-datadog-trace-id") == "HTTP_X_DATADOG_TRACE_ID"


TRACE_ID = 171395628812617415352188477958425669623
TRACE_ID_HEX = "80f198ee56343ba864fe8b2a57d3eff7"

# for testing with other propagation styles
TRACECONTEXT_HEADERS_VALID_BASIC = {
    _HTTP_HEADER_TRACEPARENT: "00-80f198ee56343ba864fe8b2a57d3eff7-00f067aa0ba902b7-01",
    _HTTP_HEADER_TRACESTATE: "dd=s:2;o:rum",
}

TRACECONTEXT_HEADERS_VALID = {
    _HTTP_HEADER_TRACEPARENT: "00-80f198ee56343ba864fe8b2a57d3eff7-00f067aa0ba902b7-01",
    _HTTP_HEADER_TRACESTATE: "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
}


@pytest.mark.parametrize(
    "sampling_priority_tp,sampling_priority_ts,expected_sampling_priority",
    [
        (0, 0, 0),
        (0, 1, 0),
        (0, 2, 0),
        (1, 0, 1),
        (1, 1, 1),
        (1, 2, 2),
        (0, None, 0),
        (1, None, 1),
        (0, -1, -1),
        (1, -1, 1),
        (1, -2, 1),
        (0, -2, -2),
    ],
)
def test_tracecontext_get_sampling_priority(sampling_priority_tp, sampling_priority_ts, expected_sampling_priority):
    traceparent_values = _TraceContext._get_sampling_priority(sampling_priority_tp, sampling_priority_ts)
    assert traceparent_values == expected_sampling_priority


@pytest.mark.parametrize(
    "headers,expected_tuple,expected_logging,expected_exception",
    [
        (
            "00-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            # tp, trace_id, span_id, sampling_priority
            (TRACE_ID, 67667974448284343, 1),
            None,
            None,
        ),
        (
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01",
            None,
            None,
            ValueError,
        ),
        (
            "00-%s-0000000000000000-01" % (TRACE_ID_HEX,),
            None,
            None,
            ValueError,
        ),
        (
            "00-%s-00f067aa0ba902b7-00" % (TRACE_ID_HEX,),
            # tp, trace_id, span_id, sampling_priority
            (TRACE_ID, 67667974448284343, 0),
            None,
            None,
        ),
        (
            "01-%s-00f067aa0ba902b7-01-what-the-future-looks-like" % (TRACE_ID_HEX,),
            # tp, trace_id, span_id, sampling_priority
            (TRACE_ID, 67667974448284343, 1),
            ["unsupported traceparent version:'01', still attempting to parse"],
            None,
        ),
        (
            "00-%s-00f067aa0ba902b7-01-v00-can-not-have-future-values" % (TRACE_ID_HEX,),
            # tp, trace_id, span_id, sampling_priority
            (TRACE_ID, 67667974448284343, 1),
            [],
            ValueError,
        ),
        (
            "0-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            # tp, trace_id, span_id, sampling_priority
            None,
            [],
            ValueError,
        ),
        (
            "ff-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            # tp, trace_id, span_id, sampling_priority
            None,
            [],
            ValueError,
        ),
        (
            "00-4BF92K3577B34dA6C3ce929d0e0e4736-00f067aa0ba902b7-01",
            # tp, trace_id, span_id, sampling_priority
            None,
            [],
            ValueError,
        ),
        (
            "00-f92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            # tp, trace_id, span_id, sampling_priority
            None,
            [],
            ValueError,
        ),
        (  # we still parse the trace flag and analyze the it as a bit field
            "00-%s-00f067aa0ba902b7-02" % (TRACE_ID_HEX,),
            # tp, trace_id, span_id, sampling_priority
            (TRACE_ID, 67667974448284343, 0),
            [],
            None,
        ),
    ],
    ids=[
        "traceflag_01",
        "invalid_0_value_for_trace_id",
        "invalid_0_value_for_span_id",
        "traceflag_00",
        "unsupported_version",
        "version_00_with_unsupported_trailing_values",
        "short_version",
        "invalid_version",
        "traceparent_contains_uppercase_chars",
        "short_trace_id",
        "unknown_trace_flag",
    ],
)
def test_extract_traceparent(caplog, headers, expected_tuple, expected_logging, expected_exception):
    with caplog.at_level(logging.DEBUG):
        if expected_exception:
            with pytest.raises(expected_exception):
                _TraceContext._get_traceparent_values(headers)
        else:
            traceparent_values = _TraceContext._get_traceparent_values(headers)
            assert traceparent_values == expected_tuple

        if caplog.text or expected_logging:
            for expected_log in expected_logging:
                assert expected_log in caplog.text


@pytest.mark.parametrize(
    "ts_string,expected_tuple,expected_logging,expected_exception",
    [
        (
            "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE,mako=s:2;o:rum;",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                2,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                "rum",
            ),
            None,
            None,
        ),
        (
            "dd=s:0;o:rum;t.dm:-4;t.usr.id:baz64",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                0,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                "rum",
            ),
            None,
            None,
        ),
        (
            "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz64",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                2,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                "rum",
            ),
            None,
            None,
        ),
        (
            "dd=o:rum;t.dm:-4;t.usr.id:baz64",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                None,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                "rum",
            ),
            None,
            None,
        ),
        (
            "dd=s:-1;o:rum;t.dm:-4;t.usr.id:baz64",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                -1,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                "rum",
            ),
            None,
            None,
        ),
        (
            "dd=s:2;t.dm:-4;t.usr.id:baz64",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                2,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                None,
            ),
            None,
            None,
        ),
        (
            "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz64;t.unk:unk,congo=t61rcWkgMzE,mako=s:2;o:rum;",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                2,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                    "_dd.p.unk": "unk",
                },
                "rum",
            ),
            None,
            None,
        ),
        (
            "congo=t61rcWkgMzE,mako=s:2;o:rum;",
            # sampling_priority_ts, other_propagated_tags, origin
            (None, {}, None),
            None,
            None,
        ),
        (
            "dd=s:2;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE,mako=s:2;o:rum;",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                2,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                },
                None,
            ),
            None,
            None,
        ),
        (
            "dd=invalid,congo=123",
            None,
            ["received invalid dd header value in tracestate: 'dd=invalid,congo=123'"],
            ValueError,
        ),
        (  # "ts_string,expected_tuple,expected_logging,expected_exception",
            "dd=foo|bar:hi|l¢¢¢¢¢¢:",
            (None, {}, None),
            None,
            None,
        ),
        (
            "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz6~~~4",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                2,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz6===4",
                },
                "rum",
            ),
            None,
            None,
        ),
        (
            "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz:6:4",
            # sampling_priority_ts, other_propagated_tags, origin
            (
                2,
                {
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz:6:4",
                },
                "rum",
            ),
            None,
            None,
        ),
    ],
    ids=[
        "tracestate_with_additional_list_members",
        "tracestate_with_0_sampling_priority",
        "tracestate_with_only_dd_list_member",
        "no_sampling_priority",
        "tracestate_with_negative_sampling_priority",
        "tracestate_with_no_origin",
        "tracestate_with_unknown_t._values",
        "tracestate_with_no_dd_list_member",
        "tracestate_no_origin",
        "tracestate_invalid_dd_list_member",
        "tracestate_invalid_tracestate_char_outside_ascii_range_20-70",
        "tracestate_tilda_replaced_with_equals",
        "tracestate_colon_acceptable_char_in_value",
    ],
)
def test_extract_tracestate(caplog, ts_string, expected_tuple, expected_logging, expected_exception):
    with caplog.at_level(logging.DEBUG):
        if expected_exception:
            with pytest.raises(expected_exception):
                tracestate_values = _TraceContext._get_tracestate_values(ts_string.split(","))
                assert tracestate_values == expected_tuple
        else:
            tracestate_values = _TraceContext._get_tracestate_values(ts_string.split(","))
            assert tracestate_values == expected_tuple
            if caplog.text or expected_logging:
                for expected_log in expected_logging:
                    assert expected_log in caplog.text


@pytest.mark.parametrize(
    "headers,expected_context",
    [
        (
            TRACECONTEXT_HEADERS_VALID,
            {
                "trace_id": TRACE_ID,
                "span_id": 67667974448284343,
                "meta": {
                    "tracestate": "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
                    "_dd.p.dm": "-4",
                    "_dd.p.usr.id": "baz64",
                    "_dd.origin": "rum",
                    "traceparent": TRACECONTEXT_HEADERS_VALID[_HTTP_HEADER_TRACEPARENT],
                },
                "metrics": {"_sampling_priority_v1": 2},
            },
        ),
        (
            TRACECONTEXT_HEADERS_VALID_BASIC,
            {
                "trace_id": TRACE_ID,
                "span_id": 67667974448284343,
                "meta": {
                    "tracestate": "dd=s:2;o:rum",
                    "_dd.origin": "rum",
                    "traceparent": TRACECONTEXT_HEADERS_VALID_BASIC[_HTTP_HEADER_TRACEPARENT],
                },
                "metrics": {"_sampling_priority_v1": 2},
            },
        ),
        (
            {
                _HTTP_HEADER_TRACEPARENT: "00-4bae0e4736-00f067aa0ba902b7-01",
                _HTTP_HEADER_TRACESTATE: "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
            },
            {"trace_id": None, "span_id": None, "meta": {}, "metrics": {}},
        ),
        (
            {
                _HTTP_HEADER_TRACEPARENT: "00-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            },
            {
                "trace_id": TRACE_ID,
                "span_id": 67667974448284343,
                "meta": {
                    "traceparent": "00-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
                },
                "metrics": {"_sampling_priority_v1": 1.0},
            },
        ),
        (
            {
                _HTTP_HEADER_TRACESTATE: "dd=s:2;o:rum;t.dm:-4;t.usr.id:baz64,congo=t61rcWkgMzE",
            },
            {"trace_id": None, "span_id": None, "meta": {}, "metrics": {}},
        ),
    ],
    ids=[
        "valid_headers_with_multiple_tracestate_list_members",
        "valid_headers_only_dd_tracestate_list_member",
        "invalid_traceparent",
        "traceparent_no_tracestate",
        "tracestate_no_traceparent",
    ],
)
def test_extract_tracecontext(headers, expected_context):
    overrides = {"_propagation_style_extract": [_PROPAGATION_STYLE_W3C_TRACECONTEXT]}
    with override_global_config(overrides):
        context = HTTPPropagator.extract(headers)
        assert context == Context(**expected_context)


CONTEXT_EMPTY = {
    "trace_id": None,
    "span_id": None,
    "sampling_priority": None,
    "dd_origin": None,
}
DATADOG_HEADERS_VALID = {
    HTTP_HEADER_TRACE_ID: "13088165645273925489",
    HTTP_HEADER_PARENT_ID: "5678",
    HTTP_HEADER_SAMPLING_PRIORITY: "1",
    HTTP_HEADER_ORIGIN: "synthetics",
}
DATADOG_HEADERS_INVALID = {
    HTTP_HEADER_TRACE_ID: "13088165645273925489",  # still valid
    HTTP_HEADER_PARENT_ID: "parent_id",
    HTTP_HEADER_SAMPLING_PRIORITY: "sample",
}
B3_HEADERS_VALID = {
    _HTTP_HEADER_B3_TRACE_ID: "80f198ee56343ba864fe8b2a57d3eff7",
    _HTTP_HEADER_B3_SPAN_ID: "a2fb4a1d1a96d312",
    _HTTP_HEADER_B3_SAMPLED: "1",
}
B3_HEADERS_INVALID = {
    _HTTP_HEADER_B3_TRACE_ID: "NON_HEX_VALUE",
    _HTTP_HEADER_B3_SPAN_ID: "NON_HEX",
    _HTTP_HEADER_B3_SAMPLED: "3",  # unexpected sampling value
}
B3_SINGLE_HEADERS_VALID = {
    _HTTP_HEADER_B3_SINGLE: "80f198ee56343ba864fe8b2a57d3eff7-e457b5a2e4d86bd1-1",
}
B3_SINGLE_HEADERS_INVALID = {
    _HTTP_HEADER_B3_SINGLE: "NON_HEX_VALUE-e457b5a2e4d86bd1-1",
}


ALL_HEADERS = {}
ALL_HEADERS.update(DATADOG_HEADERS_VALID)
ALL_HEADERS.update(B3_HEADERS_VALID)
ALL_HEADERS.update(B3_SINGLE_HEADERS_VALID)

EXTRACT_FIXTURES = [
    # Datadog headers
    (
        "valid_datadog_default",
        None,
        DATADOG_HEADERS_VALID,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "valid_datadog_default_wsgi",
        None,
        {get_wsgi_header(name): value for name, value in DATADOG_HEADERS_VALID.items()},
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "invalid_datadog",
        [PROPAGATION_STYLE_DATADOG],
        DATADOG_HEADERS_INVALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_datadog_explicit_style",
        [PROPAGATION_STYLE_DATADOG],
        DATADOG_HEADERS_VALID,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "invalid_datadog_negative_trace_id",
        [PROPAGATION_STYLE_DATADOG],
        {
            HTTP_HEADER_TRACE_ID: "-1",
            HTTP_HEADER_PARENT_ID: "5678",
            HTTP_HEADER_SAMPLING_PRIORITY: "1",
            HTTP_HEADER_ORIGIN: "synthetics",
        },
        CONTEXT_EMPTY,
    ),
    (
        "valid_datadog_explicit_style_wsgi",
        [PROPAGATION_STYLE_DATADOG],
        {get_wsgi_header(name): value for name, value in DATADOG_HEADERS_VALID.items()},
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "valid_datadog_all_styles",
        [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE],
        DATADOG_HEADERS_VALID,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "valid_datadog_no_datadog_style",
        [PROPAGATION_STYLE_B3_MULTI],
        DATADOG_HEADERS_VALID,
        CONTEXT_EMPTY,
    ),
    # B3 headers
    (
        "valid_b3_simple",
        [PROPAGATION_STYLE_B3_MULTI],
        B3_HEADERS_VALID,
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_wsgi",
        [PROPAGATION_STYLE_B3_MULTI],
        {get_wsgi_header(name): value for name, value in B3_HEADERS_VALID.items()},
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_flags",
        [PROPAGATION_STYLE_B3_MULTI],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
            _HTTP_HEADER_B3_SPAN_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_SPAN_ID],
            _HTTP_HEADER_B3_FLAGS: "1",
        },
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 2,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_with_parent_id",
        [PROPAGATION_STYLE_B3_MULTI],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
            _HTTP_HEADER_B3_SPAN_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_SPAN_ID],
            _HTTP_HEADER_B3_SAMPLED: "0",
            "X-B3-ParentSpanId": "05e3ac9a4f6e3b90",
        },
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 0,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_only_trace_and_span_id",
        [PROPAGATION_STYLE_B3_MULTI],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
            _HTTP_HEADER_B3_SPAN_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_SPAN_ID],
        },
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": None,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_only_trace_id",
        [PROPAGATION_STYLE_B3_MULTI],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
        },
        {
            "trace_id": TRACE_ID,
            "span_id": None,
            "sampling_priority": None,
            "dd_origin": None,
        },
    ),
    (
        "invalid_b3",
        [PROPAGATION_STYLE_B3_MULTI],
        B3_HEADERS_INVALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_b3_default_style",
        None,
        B3_HEADERS_VALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_b3_no_b3_style",
        [PROPAGATION_STYLE_B3_SINGLE],
        B3_HEADERS_VALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_b3_all_styles",
        [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE],
        B3_HEADERS_VALID,
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    # B3 single header
    (
        "valid_b3_single_header_simple",
        [PROPAGATION_STYLE_B3_SINGLE],
        B3_SINGLE_HEADERS_VALID,
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_simple",
        [PROPAGATION_STYLE_B3_SINGLE],
        {
            get_wsgi_header(_HTTP_HEADER_B3_SINGLE): B3_SINGLE_HEADERS_VALID[_HTTP_HEADER_B3_SINGLE],
        },
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_simple",
        [PROPAGATION_STYLE_B3_SINGLE],
        {
            get_wsgi_header(_HTTP_HEADER_B3_SINGLE): B3_SINGLE_HEADERS_VALID[_HTTP_HEADER_B3_SINGLE],
        },
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_only_sampled",
        [PROPAGATION_STYLE_B3_SINGLE],
        {
            _HTTP_HEADER_B3_SINGLE: "1",
        },
        {
            "trace_id": None,
            "span_id": None,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_only_trace_and_span_id",
        [PROPAGATION_STYLE_B3_SINGLE],
        {
            _HTTP_HEADER_B3_SINGLE: "80f198ee56343ba864fe8b2a57d3eff7-e457b5a2e4d86bd1",
        },
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": None,
            "dd_origin": None,
        },
    ),
    (
        "invalid_b3_single_header",
        [PROPAGATION_STYLE_B3_SINGLE],
        B3_SINGLE_HEADERS_INVALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_b3_single_header_all_styles",
        [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE],
        B3_SINGLE_HEADERS_VALID,
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_extra_data",
        [PROPAGATION_STYLE_B3_SINGLE],
        {_HTTP_HEADER_B3_SINGLE: B3_SINGLE_HEADERS_VALID[_HTTP_HEADER_B3_SINGLE] + "-05e3ac9a4f6e3b90-extra-data-here"},
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_default_style",
        None,
        B3_SINGLE_HEADERS_VALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_b3_single_header_no_b3_single_header_style",
        [PROPAGATION_STYLE_B3_MULTI],
        B3_SINGLE_HEADERS_VALID,
        CONTEXT_EMPTY,
    ),
    # All valid headers
    (
        "valid_all_headers_default_style",
        None,
        ALL_HEADERS,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        # Since Datadog format comes first in [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI,
        #  PROPAGATION_STYLE_B3_SINGLE] we use it
        "valid_all_headers_all_styles",
        [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE],
        ALL_HEADERS,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "valid_all_headers_all_styles_wsgi",
        [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE],
        {get_wsgi_header(name): value for name, value in ALL_HEADERS.items()},
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "valid_all_headers_datadog_style",
        [PROPAGATION_STYLE_DATADOG],
        ALL_HEADERS,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "valid_all_headers_datadog_style_wsgi",
        [PROPAGATION_STYLE_DATADOG],
        {get_wsgi_header(name): value for name, value in ALL_HEADERS.items()},
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        "valid_all_headers_b3_style",
        [PROPAGATION_STYLE_B3_MULTI],
        ALL_HEADERS,
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_b3_style_wsgi",
        [PROPAGATION_STYLE_B3_MULTI],
        {get_wsgi_header(name): value for name, value in ALL_HEADERS.items()},
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_both_b3_styles",
        [PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE],
        ALL_HEADERS,
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_b3_single_style",
        [PROPAGATION_STYLE_B3_SINGLE],
        ALL_HEADERS,
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        # name, styles, headers, expected_context,
        "none_style",
        [_PROPAGATION_STYLE_NONE],
        ALL_HEADERS,
        {
            "trace_id": None,
            "span_id": None,
            "sampling_priority": None,
            "dd_origin": None,
        },
    ),
    (
        # name, styles, headers, expected_context,
        "none_and_other_prop_style_still_extracts",
        [PROPAGATION_STYLE_DATADOG, _PROPAGATION_STYLE_NONE],
        ALL_HEADERS,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    # Testing that order matters
    (
        "order_matters_B3_SINGLE_HEADER_first",
        [PROPAGATION_STYLE_B3_SINGLE, PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_DATADOG],
        B3_SINGLE_HEADERS_VALID,
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "order_matters_B3_first",
        [PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE, PROPAGATION_STYLE_DATADOG],
        B3_HEADERS_VALID,
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "order_matters_B3_second_no_Datadog_headers",
        [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI],
        B3_HEADERS_VALID,
        {
            "trace_id": TRACE_ID,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_b3_single_style_wsgi",
        [PROPAGATION_STYLE_B3_SINGLE],
        {get_wsgi_header(name): value for name, value in ALL_HEADERS.items()},
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_no_style",
        [],
        ALL_HEADERS,
        CONTEXT_EMPTY,
    ),
    (
        "valid_all_headers_no_style_wsgi",
        [],
        {get_wsgi_header(name): value for name, value in ALL_HEADERS.items()},
        CONTEXT_EMPTY,
    ),
]

# Only add fixtures here if they can't pass both test_propagation_extract_env
#  and test_propagation_extract_w_config
EXTRACT_FIXTURES_ENV_ONLY = [
    (
        # tracecontext propagation sets additional meta data that
        # can't be tested correctly via test_propagation_extract_w_config. It is tested separately
        "valid_tracecontext_simple",
        [_PROPAGATION_STYLE_W3C_TRACECONTEXT],
        TRACECONTEXT_HEADERS_VALID_BASIC,
        {
            "trace_id": TRACE_ID,
            "span_id": 67667974448284343,
            "sampling_priority": 2,
            "dd_origin": "rum",
        },
    ),
]


@pytest.mark.parametrize("name,styles,headers,expected_context", EXTRACT_FIXTURES + EXTRACT_FIXTURES_ENV_ONLY)
def test_propagation_extract_env(name, styles, headers, expected_context, run_python_code_in_subprocess):
    # Execute the test code in isolation to ensure env variables work as expected
    code = """
import json

from ddtrace.propagation.http import HTTPPropagator


context = HTTPPropagator.extract({!r})
if context is None:
    print("null")
else:
    print(json.dumps({{
      "trace_id": context.trace_id,
      "span_id": context.span_id,
      "sampling_priority": context.sampling_priority,
      "dd_origin": context.dd_origin,
    }}))
    """.format(
        headers
    )
    env = os.environ.copy()
    if styles is not None:
        env["DD_TRACE_PROPAGATION_STYLE"] = ",".join(styles)
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)

    result = json.loads(stdout.decode())
    assert result == expected_context


@pytest.mark.parametrize("name,styles,headers,expected_context", EXTRACT_FIXTURES)
def test_propagation_extract_w_config(name, styles, headers, expected_context, run_python_code_in_subprocess):
    # Setting via ddtrace.config works as expected too
    # DEV: This also helps us get code coverage reporting
    overrides = {}
    if styles is not None:
        overrides["_propagation_style_extract"] = styles
        with override_global_config(overrides):
            context = HTTPPropagator.extract(headers)
            assert context == Context(**expected_context)


EXTRACT_OVERRIDE_FIXTURES = [
    (
        "valid_all_headers_b3_single_override",
        [PROPAGATION_STYLE_B3_MULTI],
        [PROPAGATION_STYLE_B3_SINGLE],
        ALL_HEADERS,
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_datadog_override",
        [PROPAGATION_STYLE_B3_MULTI],
        [PROPAGATION_STYLE_DATADOG],
        ALL_HEADERS,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    # The empty list provided for DD_TRACE_PROPAGATION_STYLE_EXTRACT overrides
    #  the b3 value for DD_TRACE_PROPAGATION_STYLE
    (
        "valid_all_headers_no_style_override",
        [PROPAGATION_STYLE_B3_MULTI],
        [],
        ALL_HEADERS,
        CONTEXT_EMPTY,
    ),
    (
        "valid_all_headers_b3_single_header_override_default",
        None,
        [PROPAGATION_STYLE_B3_SINGLE],
        ALL_HEADERS,
        {
            "trace_id": TRACE_ID,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
]


@pytest.mark.parametrize("name,styles,styles_extract,headers,expected_context", EXTRACT_OVERRIDE_FIXTURES)
def test_DD_TRACE_PROPAGATION_STYLE_EXTRACT_overrides_DD_TRACE_PROPAGATION_STYLE(
    name, styles, styles_extract, headers, expected_context, run_python_code_in_subprocess
):
    # Execute the test code in isolation to ensure env variables work as expected
    code = """
import json

from ddtrace.propagation.http import HTTPPropagator


context = HTTPPropagator.extract({!r})
if context is None:
    print("null")
else:
    print(json.dumps({{
      "trace_id": context.trace_id,
      "span_id": context.span_id,
      "sampling_priority": context.sampling_priority,
      "dd_origin": context.dd_origin,
    }}))
    """.format(
        headers
    )
    env = os.environ.copy()
    if styles is not None:
        env["DD_TRACE_PROPAGATION_STYLE"] = ",".join(styles)
    if styles_extract is not None:
        env["DD_TRACE_PROPAGATION_STYLE_EXTRACT"] = ",".join(styles_extract)

    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    result = json.loads(stdout.decode())
    assert result == expected_context


VALID_DATADOG_CONTEXT = {
    "trace_id": 13088165645273925489,
    "span_id": 8185124618007618416,
    "sampling_priority": 1,
    "dd_origin": "synthetics",
}


VALID_USER_KEEP_CONTEXT = {
    "trace_id": 13088165645273925489,
    "span_id": 8185124618007618416,
    "sampling_priority": 2,
}
VALID_AUTO_REJECT_CONTEXT = {
    "trace_id": 13088165645273925489,
    "span_id": 8185124618007618416,
    "sampling_priority": 0,
}
INJECT_FIXTURES = [
    # No style defined
    (
        "valid_no_style",
        [],
        VALID_DATADOG_CONTEXT,
        {},
    ),
    # Invalid context
    (
        "invalid_default_style",
        None,
        {},
        {},
    ),
    (
        "invalid_datadog_style",
        [PROPAGATION_STYLE_DATADOG],
        {},
        {},
    ),
    (
        "invalid_b3_style",
        [PROPAGATION_STYLE_B3_MULTI],
        {},
        {},
    ),
    (
        "invalid_b3_single_style",
        [PROPAGATION_STYLE_B3_SINGLE],
        {},
        {},
    ),
    (
        "invalid_all_styles",
        [PROPAGATION_STYLE_DATADOG, PROPAGATION_STYLE_B3_MULTI, PROPAGATION_STYLE_B3_SINGLE],
        {},
        {},
    ),
    # Default/Datadog style
    (
        "valid_default_style",
        None,
        VALID_DATADOG_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "1",
            HTTP_HEADER_ORIGIN: "synthetics",
            _HTTP_HEADER_TRACESTATE: "dd=s:1;o:synthetics",
            _HTTP_HEADER_TRACEPARENT: "00-0000000000000000b5a2814f70060771-7197677932a62370-01",
        },
    ),
    (
        "valid_default_style_user_keep",
        None,
        VALID_USER_KEEP_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "2",
            _HTTP_HEADER_TRACESTATE: "dd=s:2",
            _HTTP_HEADER_TRACEPARENT: "00-0000000000000000b5a2814f70060771-7197677932a62370-01",
        },
    ),
    (
        "valid_default_style_auto_reject",
        None,
        VALID_AUTO_REJECT_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "0",
            _HTTP_HEADER_TRACESTATE: "dd=s:0",
            _HTTP_HEADER_TRACEPARENT: "00-0000000000000000b5a2814f70060771-7197677932a62370-00",
        },
    ),
    (
        "valid_datadog_style",
        [PROPAGATION_STYLE_DATADOG],
        VALID_DATADOG_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "1",
            HTTP_HEADER_ORIGIN: "synthetics",
        },
    ),
    (
        "valid_datadog_style_user_keep",
        [PROPAGATION_STYLE_DATADOG],
        VALID_USER_KEEP_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "2",
        },
    ),
    (
        "valid_datadog_style_auto_reject",
        [PROPAGATION_STYLE_DATADOG],
        VALID_AUTO_REJECT_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "0",
        },
    ),
    (
        "valid_datadog_style_no_sampling_priority",
        [PROPAGATION_STYLE_DATADOG],
        {
            "trace_id": VALID_DATADOG_CONTEXT["trace_id"],
            "span_id": VALID_DATADOG_CONTEXT["span_id"],
        },
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
        },
    ),
    # B3 only
    (
        "valid_b3_style",
        [PROPAGATION_STYLE_B3_MULTI],
        VALID_DATADOG_CONTEXT,
        {
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SAMPLED: "1",
        },
    ),
    (
        "valid_b3_style_user_keep",
        [PROPAGATION_STYLE_B3_MULTI],
        VALID_USER_KEEP_CONTEXT,
        {
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_FLAGS: "1",
        },
    ),
    (
        "valid_b3_style_auto_reject",
        [PROPAGATION_STYLE_B3_MULTI],
        VALID_AUTO_REJECT_CONTEXT,
        {
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SAMPLED: "0",
        },
    ),
    (
        "valid_b3_style_no_sampling_priority",
        [PROPAGATION_STYLE_B3_MULTI],
        {
            "trace_id": VALID_DATADOG_CONTEXT["trace_id"],
            "span_id": VALID_DATADOG_CONTEXT["span_id"],
        },
        {
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
        },
    ),
    # B3 Single Header
    (
        "valid_b3_single_style",
        [PROPAGATION_STYLE_B3_SINGLE],
        VALID_DATADOG_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-1"},
    ),
    # we want to make sure that if the Datadog trace_id or span_id is not
    # the standard length int we'd expect, we pad the value with 0s so it's still a valid b3 header
    (
        "valid_b3_single_style_in_need_of_padding",
        [PROPAGATION_STYLE_B3_SINGLE],
        {
            "trace_id": 123,
            "span_id": 4567,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
        {_HTTP_HEADER_B3_SINGLE: "000000000000007b-00000000000011d7-1"},
    ),
    (
        "valid_b3_single_style_user_keep",
        [PROPAGATION_STYLE_B3_SINGLE],
        VALID_USER_KEEP_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-d"},
    ),
    (
        "valid_b3_single_style_auto_reject",
        [PROPAGATION_STYLE_B3_SINGLE],
        VALID_AUTO_REJECT_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-0"},
    ),
    (
        "valid_b3_single_style_no_sampling_priority",
        [PROPAGATION_STYLE_B3_SINGLE],
        {
            "trace_id": VALID_DATADOG_CONTEXT["trace_id"],
            "span_id": VALID_DATADOG_CONTEXT["span_id"],
        },
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370"},
    ),
    # None style
    (
        "none_propagation_style_does_not_modify_header",
        [_PROPAGATION_STYLE_NONE],
        {
            "trace_id": VALID_DATADOG_CONTEXT["trace_id"],
            "span_id": VALID_DATADOG_CONTEXT["span_id"],
        },
        {},
    ),
    # if another propagation style is specified in addition to none, we should inject those headers
    (
        "none_propgagtion_with_valid_datadog_style",
        [_PROPAGATION_STYLE_NONE, PROPAGATION_STYLE_DATADOG],
        VALID_DATADOG_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "1",
            HTTP_HEADER_ORIGIN: "synthetics",
        },
    ),
    # tracecontext
    (
        "valid_tracecontext",
        [_PROPAGATION_STYLE_W3C_TRACECONTEXT],
        {
            "trace_id": TRACE_ID,
            "span_id": 67667974448284343,
            "meta": {
                "tracestate": "dd=s:2;o:rum",
                "_dd.origin": "rum",
                "traceparent": TRACECONTEXT_HEADERS_VALID_BASIC[_HTTP_HEADER_TRACEPARENT],
            },
            "metrics": {"_sampling_priority_v1": 2},
        },
        TRACECONTEXT_HEADERS_VALID_BASIC,
    ),
    (
        "only_traceparent",
        [_PROPAGATION_STYLE_W3C_TRACECONTEXT],
        {
            "trace_id": TRACE_ID,
            "span_id": 67667974448284343,
            "meta": {
                "traceparent": "00-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            },
        },
        {_HTTP_HEADER_TRACEPARENT: "00-%s-00f067aa0ba902b7-00" % (TRACE_ID_HEX,)},
    ),
    (
        "only_tracestate",
        [_PROPAGATION_STYLE_W3C_TRACECONTEXT],
        {
            "meta": {
                "tracestate": "dd=s:2;o:rum",
                "_dd.origin": "rum",
                "traceparent": "00-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            },
            "metrics": {"_sampling_priority_v1": 2},
        },
        {},
    ),
    (
        "no_context_traceparent",
        [_PROPAGATION_STYLE_W3C_TRACECONTEXT],
        {
            "trace_id": TRACE_ID,
            "span_id": 67667974448284343,
            "meta": {
                "tracestate": "dd=s:2;o:rum",
                "_dd.origin": "rum",
            },
            "metrics": {"_sampling_priority_v1": 2},
        },
        {
            _HTTP_HEADER_TRACEPARENT: "00-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            _HTTP_HEADER_TRACESTATE: "dd=s:2;o:rum",
        },
    ),
    (
        "tracestate_additional_list_members",
        [_PROPAGATION_STYLE_W3C_TRACECONTEXT],
        {
            "trace_id": TRACE_ID,
            "span_id": 67667974448284343,
            "meta": {
                "tracestate": "dd=s:2;o:rum,congo=baz123",
                "_dd.origin": "rum",
            },
            "metrics": {"_sampling_priority_v1": 2},
        },
        {
            _HTTP_HEADER_TRACEPARENT: "00-%s-00f067aa0ba902b7-01" % (TRACE_ID_HEX,),
            _HTTP_HEADER_TRACESTATE: "dd=s:2;o:rum,congo=baz123",
        },
    ),
    # All styles
    (
        "valid_all_styles",
        [
            PROPAGATION_STYLE_DATADOG,
            PROPAGATION_STYLE_B3_MULTI,
            PROPAGATION_STYLE_B3_SINGLE,
            _PROPAGATION_STYLE_W3C_TRACECONTEXT,
        ],
        VALID_DATADOG_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "1",
            HTTP_HEADER_ORIGIN: "synthetics",
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SAMPLED: "1",
            _HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-1",
            _HTTP_HEADER_TRACEPARENT: "00-0000000000000000b5a2814f70060771-7197677932a62370-01",
            _HTTP_HEADER_TRACESTATE: "dd=s:1;o:synthetics",
        },
    ),
    (
        "valid_all_styles_user_keep",
        [
            PROPAGATION_STYLE_DATADOG,
            PROPAGATION_STYLE_B3_MULTI,
            PROPAGATION_STYLE_B3_SINGLE,
            _PROPAGATION_STYLE_W3C_TRACECONTEXT,
        ],
        VALID_USER_KEEP_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "2",
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_FLAGS: "1",
            _HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-d",
            _HTTP_HEADER_TRACEPARENT: "00-0000000000000000b5a2814f70060771-7197677932a62370-01",
            _HTTP_HEADER_TRACESTATE: "dd=s:2",
        },
    ),
    (
        "valid_all_styles_auto_reject",
        [
            PROPAGATION_STYLE_DATADOG,
            PROPAGATION_STYLE_B3_MULTI,
            PROPAGATION_STYLE_B3_SINGLE,
            _PROPAGATION_STYLE_W3C_TRACECONTEXT,
        ],
        VALID_AUTO_REJECT_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "0",
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SAMPLED: "0",
            _HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-0",
            _HTTP_HEADER_TRACEPARENT: "00-0000000000000000b5a2814f70060771-7197677932a62370-00",
            _HTTP_HEADER_TRACESTATE: "dd=s:0",
        },
    ),
    (
        "valid_all_styles_no_sampling_priority",
        [
            PROPAGATION_STYLE_DATADOG,
            PROPAGATION_STYLE_B3_MULTI,
            PROPAGATION_STYLE_B3_SINGLE,
            _PROPAGATION_STYLE_W3C_TRACECONTEXT,
        ],
        {
            "trace_id": VALID_DATADOG_CONTEXT["trace_id"],
            "span_id": VALID_DATADOG_CONTEXT["span_id"],
        },
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370",
            _HTTP_HEADER_TRACEPARENT: "00-0000000000000000b5a2814f70060771-7197677932a62370-00",
        },
    ),
]


@pytest.mark.parametrize("name,styles,context,expected_headers", INJECT_FIXTURES)
def test_propagation_inject(name, styles, context, expected_headers, run_python_code_in_subprocess):
    # Execute the test code in isolation to ensure env variables work as expected
    code = """
import json

from ddtrace.context import Context
from ddtrace.propagation.http import HTTPPropagator

context = Context(**{!r})
headers = {{}}
HTTPPropagator.inject(context, headers)

print(json.dumps(headers))
    """.format(
        context
    )

    env = os.environ.copy()
    if styles is not None:
        env["DD_TRACE_PROPAGATION_STYLE"] = ",".join(styles)
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    result = json.loads(stdout.decode())
    assert result == expected_headers

    # Setting via ddtrace.config works as expected too
    # DEV: This also helps us get code coverage reporting
    overrides = {}
    if styles is not None:
        overrides["_propagation_style_inject"] = styles
    with override_global_config(overrides):
        ctx = Context(**context)
        headers = {}
        HTTPPropagator.inject(ctx, headers)
        assert headers == expected_headers


INJECT_OVERRIDE_FIXTURES = [
    (
        "valid_b3_single_style_override",
        [PROPAGATION_STYLE_DATADOG],
        [PROPAGATION_STYLE_B3_SINGLE],
        VALID_DATADOG_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-1"},
    ),
    (
        "none_override",
        [PROPAGATION_STYLE_DATADOG],
        [],
        VALID_DATADOG_CONTEXT,
        {},
    ),
    (
        "valid_b3_single_style_override_none",
        [],
        [PROPAGATION_STYLE_B3_SINGLE],
        VALID_DATADOG_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-1"},
    ),
]


@pytest.mark.parametrize("name,styles,styles_inject,context,expected_headers", INJECT_OVERRIDE_FIXTURES)
def test_DD_TRACE_PROPAGATION_STYLE_INJECT_overrides_DD_TRACE_PROPAGATION_STYLE(
    name, styles, styles_inject, context, expected_headers, run_python_code_in_subprocess
):
    # Execute the test code in isolation to ensure env variables work as expected
    code = """
import json

from ddtrace.context import Context
from ddtrace.propagation.http import HTTPPropagator

context = Context(**{!r})
headers = {{}}
HTTPPropagator.inject(context, headers)

print(json.dumps(headers))
    """.format(
        context
    )

    env = os.environ.copy()
    if styles is not None:
        env["DD_TRACE_PROPAGATION_STYLE"] = ",".join(styles)
    if styles_inject is not None:
        env["DD_TRACE_PROPAGATION_STYLE_INJECT"] = ",".join(styles_inject)
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    result = json.loads(stdout.decode())
    assert result == expected_headers
