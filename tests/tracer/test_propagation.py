import itertools
from unittest import TestCase

import pytest

from ddtrace import config as global_config
from ddtrace.context import Context
from ddtrace.propagation import b3
from ddtrace.propagation import datadog
from ddtrace.propagation import http
from ddtrace.propagation.utils import extract_header_value
from ddtrace.propagation.utils import get_wsgi_header
from tests.utils import DummyTracer


NOT_SET = object()


class TestHttpPropagation(TestCase):
    def setUp(self):
        global_config.propagation_style_extract = ["datadog"]
        global_config.propagation_style_inject = ["datadog"]
        self.tracer = DummyTracer()

    def test_inject_all_propagators(self):
        ctx = Context(trace_id=1234, sampling_priority=1, dd_origin="synthetics")
        self.tracer.context_provider.activate(ctx)
        global_config.propagation_style_inject = ["b3", "datadog"]

        with self.tracer.trace("global_root_span") as span:
            headers = {}
            http.HTTPPropagator.inject(span.context, headers)

            assert int(headers[datadog.HTTP_HEADER_TRACE_ID]) == span.trace_id
            assert int(headers[datadog.HTTP_HEADER_PARENT_ID]) == span.span_id
            assert int(headers[datadog.HTTP_HEADER_SAMPLING_PRIORITY]) == span.context.sampling_priority
            assert headers[datadog.HTTP_HEADER_ORIGIN] == span.context.dd_origin
            assert int(headers[b3.HTTP_HEADER_TRACE_ID], 16) == span.trace_id
            assert int(headers[b3.HTTP_HEADER_SPAN_ID], 16) == span.span_id
            assert int(headers[b3.HTTP_HEADER_SAMPLED]) == span.context.sampling_priority

    def test_extract_propagator_order(self):
        """Datadog extractor should take precedence when multiple extractors + headers are present"""
        global_config.propagation_style_extract = ["b3", "datadog"]
        headers = {
            "x-b3-traceid": "000000000000000000000000000004d2",
            "x-b3-spanid": "000000000000162e",
            "x-b3-sampled": "1",
            "x-datadog-trace-id": "9999",
            "x-datadog-parent-id": "5555",
            "x-datadog-sampling-priority": "1",
            "x-datadog-origin": "synthetics",
        }

        context = http.HTTPPropagator.extract(headers)
        self.tracer.context_provider.activate(context)

        with self.tracer.trace("local_root_span") as span:
            assert span.trace_id == 9999
            assert span.parent_id == 5555
            assert span.context.sampling_priority == 1

    def test_extract_b3(self):
        global_config.propagation_style_extract = ["b3"]
        headers = {
            "x-b3-traceid": "000000000000000000000000000004d2",
            "x-b3-spanid": "000000000000162e",
            "x-b3-sampled": "1",
        }

        context = http.HTTPPropagator.extract(headers)
        self.tracer.context_provider.activate(context)

        with self.tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.parent_id == 5678
            assert span.context.sampling_priority == 1


class TestDatadogHttpPropagation(TestCase):
    def setUp(self):
        self.tracer = DummyTracer()

    def test_inject(self):
        ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics")
        self.tracer.context_provider.activate(ctx)
        with self.tracer.trace("global_root_span") as span:
            headers = {}
            datadog.DatadogHTTPPropagator.inject(span.context, headers)

            assert int(headers[datadog.HTTP_HEADER_TRACE_ID]) == span.trace_id
            assert int(headers[datadog.HTTP_HEADER_PARENT_ID]) == span.span_id
            assert int(headers[datadog.HTTP_HEADER_SAMPLING_PRIORITY]) == span.context.sampling_priority
            assert headers[datadog.HTTP_HEADER_ORIGIN] == span.context.dd_origin

    def test_extract(self):
        headers = {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "5678",
            "x-datadog-sampling-priority": "1",
            "x-datadog-origin": "synthetics",
        }
        context = datadog.DatadogHTTPPropagator.extract(headers)
        self.tracer.context_provider.activate(context)

        with self.tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.parent_id == 5678
            assert span.context.sampling_priority == 1
            assert span.context.dd_origin == "synthetics"

    def test_WSGI_extract(self):
        """Ensure we support the WSGI formatted headers as well."""
        headers = {
            "HTTP_X_DATADOG_TRACE_ID": "1234",
            "HTTP_X_DATADOG_PARENT_ID": "5678",
            "HTTP_X_DATADOG_SAMPLING_PRIORITY": "1",
            "HTTP_X_DATADOG_ORIGIN": "synthetics",
        }
        context = datadog.DatadogHTTPPropagator.extract(headers)
        self.tracer.context_provider.activate(context)

        with self.tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.parent_id == 5678
            assert span.context.sampling_priority == 1
            assert span.context.dd_origin == "synthetics"


class TestB3HttpPropagation(TestCase):
    def setUp(self):
        self.tracer = DummyTracer()

    def test_inject(self):
        ctx = Context(trace_id=1234, sampling_priority=1)
        self.tracer.context_provider.activate(ctx)
        with self.tracer.trace("global_root_span") as span:
            headers = {}
            b3.B3HTTPPropagator.inject(span.context, headers)

            assert int(headers[b3.HTTP_HEADER_TRACE_ID], 16) == span.trace_id
            assert int(headers[b3.HTTP_HEADER_SPAN_ID], 16) == span.span_id
            assert int(headers[b3.HTTP_HEADER_SAMPLED]) == span.context.sampling_priority

    def test_extract(self):
        headers = {
            "x-b3-traceid": "000000000000000000000000000004d2",
            "x-b3-spanid": "000000000000162e",
            "x-b3-sampled": "1",
        }

        context = b3.B3HTTPPropagator.extract(headers)
        self.tracer.context_provider.activate(context)

        with self.tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.context.sampling_priority == 1

    def test_extract_64_bit_trace(self):
        headers = {
            "x-b3-traceid": "00000000000004d2",
            "x-b3-spanid": "000000000000162e",
            "x-b3-sampled": "1",
        }

        context = b3.B3HTTPPropagator.extract(headers)
        self.tracer.context_provider.activate(context)

        with self.tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.context.sampling_priority == 1

    def test_WSGI_extract(self):
        """Ensure we support the WSGI formatted headers as well."""
        tracer = DummyTracer()

        headers = {
            "HTTP_X_B3_TRACEID": "000000000000000000000000000004d2",
            "HTTP_X_B3_SPANID": "000000000000162e",
            "HTTP_X_B3_SAMPLED": "1",
        }

        context = b3.B3HTTPPropagator.extract(headers)
        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            assert span.trace_id == 1234
            assert span.context.sampling_priority == 1


@pytest.mark.parametrize(
    "trace_id,parent_span_id,sampling_priority,dd_origin",
    itertools.product(
        # Trace id
        ["one", None, "123.4", "", NOT_SET],
        # Parent id
        # DEV: 10 is valid for parent id but is ignored if trace id is ever invalid
        ["one", None, "123.4", "", NOT_SET, "10"],
        # Sampling priority
        ["one", None, "123.4", "", NOT_SET],
        # Origin
        [None, NOT_SET],
    ),
)
def test_extract_bad_values_datadog(trace_id, parent_span_id, sampling_priority, dd_origin):
    headers = dict()
    wsgi_headers = dict()

    if trace_id is not NOT_SET:
        headers[datadog.HTTP_HEADER_TRACE_ID] = trace_id
        wsgi_headers[get_wsgi_header(datadog.HTTP_HEADER_TRACE_ID)] = trace_id
    if parent_span_id is not NOT_SET:
        headers[datadog.HTTP_HEADER_PARENT_ID] = parent_span_id
        wsgi_headers[get_wsgi_header(datadog.HTTP_HEADER_PARENT_ID)] = parent_span_id
    if sampling_priority is not NOT_SET:
        headers[datadog.HTTP_HEADER_SAMPLING_PRIORITY] = sampling_priority
        wsgi_headers[get_wsgi_header(datadog.HTTP_HEADER_SAMPLING_PRIORITY)] = sampling_priority
    if dd_origin is not NOT_SET:
        headers[datadog.HTTP_HEADER_ORIGIN] = dd_origin
        wsgi_headers[get_wsgi_header(datadog.HTTP_HEADER_ORIGIN)] = dd_origin

    # x-datadog-*headers
    context = datadog.DatadogHTTPPropagator.extract(headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None
    assert context.dd_origin is None

    # HTTP_X_DATADOG_* headers
    context = datadog.DatadogHTTPPropagator.extract(wsgi_headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None
    assert context.dd_origin is None


@pytest.mark.parametrize(
    "trace_id,span_id,sampled",
    itertools.product(
        # Trace id
        ["one", None, "123.4", "", NOT_SET],
        # Parent id
        # DEV: 10 is valid for parent id but is ignored if trace id is ever invalid
        ["one", None, "123.4", "", NOT_SET, "10"],
        # Sampling priority
        ["one", None, "123.4", "", NOT_SET],
    ),
)
def test_extract_bad_values_b3_multi(trace_id, span_id, sampled):
    headers = dict()
    wsgi_headers = dict()

    if sampled is not NOT_SET:
        headers[b3.HTTP_HEADER_SAMPLED] = sampled
        wsgi_headers[get_wsgi_header(b3.HTTP_HEADER_SAMPLED)] = sampled
    if trace_id is not NOT_SET:
        headers[b3.HTTP_HEADER_TRACE_ID] = trace_id
        wsgi_headers[get_wsgi_header(b3.HTTP_HEADER_TRACE_ID)] = trace_id
    if span_id is not NOT_SET:
        headers[b3.HTTP_HEADER_SPAN_ID] = span_id
        wsgi_headers[get_wsgi_header(b3.HTTP_HEADER_SPAN_ID)] = span_id

    # x-b3-*headers
    context = b3.B3HTTPPropagator.extract(headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None

    # HTTP_X_B3_* headers
    context = b3.B3HTTPPropagator.extract(wsgi_headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None


@pytest.mark.parametrize(
    "trace_id,span_id,sampled",
    itertools.product(
        # Trace id
        ["one", None, "123.4", "", NOT_SET],
        # Parent id
        # DEV: 10 is valid for parent id but is ignored if trace id is ever invalid
        ["one", None, "123.4", "", NOT_SET, "10"],
        # Sampling priority
        ["one", None, "123.4", "", NOT_SET],
    ),
)
def test_extract_bad_values_b3_single(trace_id, span_id, sampled):
    headers = dict()
    wsgi_headers = dict()

    if sampled is not NOT_SET:
        if (trace_id and span_id) is not NOT_SET:
            headers[b3.HTTP_HEADER_SINGLE] = "{},{},{}".format(trace_id, span_id, sampled)
            wsgi_headers[get_wsgi_header(b3.HTTP_HEADER_SINGLE)] = "{},{},{}".format(trace_id, span_id, sampled)
        else:
            headers[b3.HTTP_HEADER_SINGLE] = "{}".format(sampled)
            wsgi_headers[get_wsgi_header(b3.HTTP_HEADER_SINGLE)] = "{}".format(sampled)
    else:
        if (trace_id and span_id) is not NOT_SET:
            headers[b3.HTTP_HEADER_SINGLE] = "{},{}".format(trace_id, span_id)
            wsgi_headers[get_wsgi_header(b3.HTTP_HEADER_SINGLE)] = "{},{}".format(trace_id, span_id)

    # b3 header
    context = b3.B3HTTPPropagator.extract(headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None

    # HTTP_B3 header
    context = b3.B3HTTPPropagator.extract(wsgi_headers)
    assert context.trace_id is None
    assert context.span_id is None
    assert context.sampling_priority is None


class TestPropagationUtils(object):
    def test_get_wsgi_header(self):
        assert get_wsgi_header("x-datadog-trace-id") == "HTTP_X_DATADOG_TRACE_ID"

    def test_extract_header_value(self):
        headers = {"foo": "1", "bar": "2", "baz": "3"}
        assert extract_header_value(possible_header_names=frozenset(["tom", "dick", "harry"]), headers=headers) is None
        assert extract_header_value(possible_header_names=frozenset(["bob", "alice", "foo"]), headers=headers) == "1"
        assert (
            extract_header_value(
                possible_header_names=frozenset(["bob", "alice", "carol"]), headers=headers, default="default"
            )
            == "default"
        )
