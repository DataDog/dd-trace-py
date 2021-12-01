from unittest import TestCase

import pytest

from ddtrace.context import Context
from ddtrace.propagation.http import HTTPPropagator
from ddtrace.propagation.http import HTTP_HEADER_ORIGIN
from ddtrace.propagation.http import HTTP_HEADER_PARENT_ID
from ddtrace.propagation.http import HTTP_HEADER_SAMPLING_PRIORITY
from ddtrace.propagation.http import HTTP_HEADER_TAGS
from ddtrace.propagation.http import HTTP_HEADER_TRACE_ID
from ddtrace.propagation.utils import get_wsgi_header
from tests.utils import DummyTracer


NOT_SET = object()


class TestHttpPropagation(TestCase):
    """
    Tests related to the ``Context`` class that hosts the trace for the
    current execution flow.
    """

    def test_inject(self):
        tracer = DummyTracer()

        # We will only pass along `_dd.p.*` tags
        meta = {"_dd.p.test": "value", "non._dd.p_tag": "value"}
        ctx = Context(trace_id=1234, sampling_priority=2, dd_origin="synthetics", meta=meta)
        tracer.context_provider.activate(ctx)
        with tracer.trace("global_root_span") as span:
            headers = {}
            HTTPPropagator.inject(span.context, headers)

            assert int(headers[HTTP_HEADER_TRACE_ID]) == span.trace_id
            assert int(headers[HTTP_HEADER_PARENT_ID]) == span.span_id
            assert int(headers[HTTP_HEADER_SAMPLING_PRIORITY]) == span.context.sampling_priority
            assert headers[HTTP_HEADER_ORIGIN] == span.context.dd_origin
            assert headers[HTTP_HEADER_TAGS] == "_dd.p.test=value"

    def test_extract(self):
        tracer = DummyTracer()

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
                "any": "tag",
            }

    def test_WSGI_extract(self):
        """Ensure we support the WSGI formatted headers as well."""
        tracer = DummyTracer()

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
                "any": "tag",
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
        headers[HTTP_HEADER_TAGS] = dd_tags
        wsgi_headers[get_wsgi_header(HTTP_HEADER_TAGS)] = dd_tags

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


class TestPropagationUtils(object):
    def test_get_wsgi_header(self):
        assert get_wsgi_header("x-datadog-trace-id") == "HTTP_X_DATADOG_TRACE_ID"
