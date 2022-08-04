# -*- coding: utf-8 -*-
import json
import os

import pytest

from ddtrace.context import Context
from ddtrace.internal.constants import PROPAGATION_STYLE_ALL
from ddtrace.internal.constants import PROPAGATION_STYLE_B3
from ddtrace.internal.constants import PROPAGATION_STYLE_B3_SINGLE_HEADER
from ddtrace.internal.constants import PROPAGATION_STYLE_DATADOG
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
    _HTTP_HEADER_B3_TRACE_ID: "463ac35c9f6413ad48485a3953bb6124",
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
        PROPAGATION_STYLE_ALL,
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
        [PROPAGATION_STYLE_B3],
        DATADOG_HEADERS_VALID,
        CONTEXT_EMPTY,
    ),
    # B3 headers
    (
        "valid_b3_simple",
        [PROPAGATION_STYLE_B3],
        B3_HEADERS_VALID,
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_wsgi",
        [PROPAGATION_STYLE_B3],
        {get_wsgi_header(name): value for name, value in B3_HEADERS_VALID.items()},
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_flags",
        [PROPAGATION_STYLE_B3],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
            _HTTP_HEADER_B3_SPAN_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_SPAN_ID],
            _HTTP_HEADER_B3_FLAGS: "1",
        },
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 2,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_with_parent_id",
        [PROPAGATION_STYLE_B3],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
            _HTTP_HEADER_B3_SPAN_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_SPAN_ID],
            _HTTP_HEADER_B3_SAMPLED: "0",
            "X-B3-ParentSpanId": "05e3ac9a4f6e3b90",
        },
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 0,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_only_trace_and_span_id",
        [PROPAGATION_STYLE_B3],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
            _HTTP_HEADER_B3_SPAN_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_SPAN_ID],
        },
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": None,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_only_trace_id",
        [PROPAGATION_STYLE_B3],
        {
            _HTTP_HEADER_B3_TRACE_ID: B3_HEADERS_VALID[_HTTP_HEADER_B3_TRACE_ID],
        },
        {
            "trace_id": 5208512171318403364,
            "span_id": None,
            "sampling_priority": None,
            "dd_origin": None,
        },
    ),
    (
        "invalid_b3",
        [PROPAGATION_STYLE_B3],
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
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        B3_HEADERS_VALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_b3_all_styles",
        PROPAGATION_STYLE_ALL,
        B3_HEADERS_VALID,
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    # B3 single header
    (
        "valid_b3_single_header_simple",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        B3_SINGLE_HEADERS_VALID,
        {
            "trace_id": 7277407061855694839,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_simple",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        {
            get_wsgi_header(_HTTP_HEADER_B3_SINGLE): B3_SINGLE_HEADERS_VALID[_HTTP_HEADER_B3_SINGLE],
        },
        {
            "trace_id": 7277407061855694839,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_simple",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        {
            get_wsgi_header(_HTTP_HEADER_B3_SINGLE): B3_SINGLE_HEADERS_VALID[_HTTP_HEADER_B3_SINGLE],
        },
        {
            "trace_id": 7277407061855694839,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_only_sampled",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
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
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        {
            _HTTP_HEADER_B3_SINGLE: "80f198ee56343ba864fe8b2a57d3eff7-e457b5a2e4d86bd1",
        },
        {
            "trace_id": 7277407061855694839,
            "span_id": 16453819474850114513,
            "sampling_priority": None,
            "dd_origin": None,
        },
    ),
    (
        "invalid_b3_single_header",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        B3_SINGLE_HEADERS_INVALID,
        CONTEXT_EMPTY,
    ),
    (
        "valid_b3_single_header_all_styles",
        PROPAGATION_STYLE_ALL,
        B3_SINGLE_HEADERS_VALID,
        {
            "trace_id": 7277407061855694839,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_b3_single_header_extra_data",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        {_HTTP_HEADER_B3_SINGLE: B3_SINGLE_HEADERS_VALID[_HTTP_HEADER_B3_SINGLE] + "-05e3ac9a4f6e3b90-extra-data-here"},
        {
            "trace_id": 7277407061855694839,
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
        [PROPAGATION_STYLE_B3],
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
        # we prefer Datadog format
        "valid_all_headers_all_styles",
        PROPAGATION_STYLE_ALL,
        ALL_HEADERS,
        {
            "trace_id": 13088165645273925489,
            "span_id": 5678,
            "sampling_priority": 1,
            "dd_origin": "synthetics",
        },
    ),
    (
        # we prefer Datadog format
        "valid_all_headers_all_styles_wsgi",
        PROPAGATION_STYLE_ALL,
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
        [PROPAGATION_STYLE_B3],
        ALL_HEADERS,
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_b3_style_wsgi",
        [PROPAGATION_STYLE_B3],
        {get_wsgi_header(name): value for name, value in ALL_HEADERS.items()},
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        # We prefer B3 over B3 single header
        "valid_all_headers_both_b3_styles",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER, PROPAGATION_STYLE_B3],
        ALL_HEADERS,
        {
            "trace_id": 5208512171318403364,
            "span_id": 11744061942159299346,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_b3_single_style",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        ALL_HEADERS,
        {
            "trace_id": 7277407061855694839,
            "span_id": 16453819474850114513,
            "sampling_priority": 1,
            "dd_origin": None,
        },
    ),
    (
        "valid_all_headers_b3_single_style_wsgi",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        {get_wsgi_header(name): value for name, value in ALL_HEADERS.items()},
        {
            "trace_id": 7277407061855694839,
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


@pytest.mark.parametrize("name,styles,headers,expected_context", EXTRACT_FIXTURES)
def test_propagation_extract(name, styles, headers, expected_context, run_python_code_in_subprocess):
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
        env["DD_TRACE_PROPAGATION_STYLE_EXTRACT"] = ",".join(styles)
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    result = json.loads(stdout.decode())
    assert result == expected_context

    # Setting via ddtrace.config works as expected too
    # DEV: This also helps us get code coverage reporting
    overrides = {}
    if styles is not None:
        overrides["_propagation_style_extract"] = set(styles)
    with override_global_config(overrides):
        context = HTTPPropagator.extract(headers)
        assert context == Context(**expected_context)


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
        [PROPAGATION_STYLE_B3],
        {},
        {},
    ),
    (
        "invalid_b3_single_style",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        {},
        {},
    ),
    (
        "invalid_all_styles",
        PROPAGATION_STYLE_ALL,
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
        None,
        VALID_USER_KEEP_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "2",
        },
    ),
    (
        "valid_datadog_style_auto_reject",
        None,
        VALID_AUTO_REJECT_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "0",
        },
    ),
    (
        "valid_datadog_style_no_sampling_priority",
        None,
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
        [PROPAGATION_STYLE_B3],
        VALID_DATADOG_CONTEXT,
        {
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SAMPLED: "1",
        },
    ),
    (
        "valid_b3_style_user_keep",
        [PROPAGATION_STYLE_B3],
        VALID_USER_KEEP_CONTEXT,
        {
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_FLAGS: "1",
        },
    ),
    (
        "valid_b3_style_auto_reject",
        [PROPAGATION_STYLE_B3],
        VALID_AUTO_REJECT_CONTEXT,
        {
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SAMPLED: "0",
        },
    ),
    (
        "valid_b3_style_no_sampling_priority",
        [PROPAGATION_STYLE_B3],
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
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        VALID_DATADOG_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-1"},
    ),
    (
        "valid_b3_single_style_user_keep",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        VALID_USER_KEEP_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-d"},
    ),
    (
        "valid_b3_single_style_auto_reject",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        VALID_AUTO_REJECT_CONTEXT,
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-0"},
    ),
    (
        "valid_b3_single_style_no_sampling_priority",
        [PROPAGATION_STYLE_B3_SINGLE_HEADER],
        {
            "trace_id": VALID_DATADOG_CONTEXT["trace_id"],
            "span_id": VALID_DATADOG_CONTEXT["span_id"],
        },
        {_HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370"},
    ),
    # All styles
    (
        "valid_all_styles",
        PROPAGATION_STYLE_ALL,
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
        },
    ),
    (
        "valid_all_styles_user_keep",
        PROPAGATION_STYLE_ALL,
        VALID_USER_KEEP_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "2",
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_FLAGS: "1",
            _HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-d",
        },
    ),
    (
        "valid_all_styles_auto_reject",
        PROPAGATION_STYLE_ALL,
        VALID_AUTO_REJECT_CONTEXT,
        {
            HTTP_HEADER_TRACE_ID: "13088165645273925489",
            HTTP_HEADER_PARENT_ID: "8185124618007618416",
            HTTP_HEADER_SAMPLING_PRIORITY: "0",
            _HTTP_HEADER_B3_TRACE_ID: "b5a2814f70060771",
            _HTTP_HEADER_B3_SPAN_ID: "7197677932a62370",
            _HTTP_HEADER_B3_SAMPLED: "0",
            _HTTP_HEADER_B3_SINGLE: "b5a2814f70060771-7197677932a62370-0",
        },
    ),
    (
        "valid_all_styles_no_sampling_priority",
        PROPAGATION_STYLE_ALL,
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
        env["DD_TRACE_PROPAGATION_STYLE_INJECT"] = ",".join(styles)
    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code, env=env)
    assert status == 0, (stdout, stderr)
    assert stderr == b"", (stdout, stderr)

    result = json.loads(stdout.decode())
    assert result == expected_headers

    # Setting via ddtrace.config works as expected too
    # DEV: This also helps us get code coverage reporting
    overrides = {}
    if styles is not None:
        overrides["_propagation_style_inject"] = set(styles)
    with override_global_config(overrides):
        ctx = Context(**context)
        headers = {}
        HTTPPropagator.inject(ctx, headers)
        assert headers == expected_headers
