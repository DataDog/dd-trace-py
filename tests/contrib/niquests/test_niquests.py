import niquests
import pytest

from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_STACK
from ddtrace.constants import ERROR_TYPE
from ddtrace.contrib.internal.niquests.patch import patch
from ddtrace.contrib.internal.niquests.patch import unpatch
from ddtrace.ext import http
from tests.utils import assert_is_measured
from tests.utils import assert_span_http_status_code
from tests.utils import override_config

from .conftest import HTTPBIN


def _pop_request_span(test_spans):
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 1
    return traces[0][0]


def _assert_client_span(span, method, path, status_code):
    assert span.name == "niquests.request"
    assert span.service == "niquests"
    assert span.resource == "{} {}".format(method, path)
    assert span.span_type == "http"
    assert span.get_tag("component") == "niquests"
    assert span.get_tag("span.kind") == "client"
    assert span.get_tag(http.METHOD) == method
    assert span.get_tag(http.URL) == HTTPBIN + path
    assert span.get_tag("out.host") == "localhost"
    assert_span_http_status_code(span, status_code)
    assert_is_measured(span)


@pytest.mark.parametrize(
    "status_code,expected_error",
    [(200, 0), (404, 0), (500, 1)],
)
def test_sync_status_semantics(patched_niquests, test_spans, status_code, expected_error):
    path = "/status/{}".format(status_code)
    response = niquests.get(HTTPBIN + path)

    assert response.status_code == status_code
    span = _pop_request_span(test_spans)
    _assert_client_span(span, "GET", path, status_code)
    assert span.error == expected_error


def test_sync_transport_error_preserves_exception(patched_niquests, test_spans):
    with pytest.raises(niquests.exceptions.ConnectionError) as raised:
        niquests.get("http://127.0.0.1:1/unreachable", timeout=0.2)

    span = _pop_request_span(test_spans)
    assert span.error == 1
    assert span.get_tag(ERROR_TYPE).endswith("ConnectionError")
    assert span.get_tag(ERROR_MSG) == str(raised.value)
    assert span.get_tag(ERROR_STACK)


def test_service_override(patched_niquests, test_spans):
    with override_config("niquests", {"service": "custom-http-client"}):
        response = niquests.get(HTTPBIN + "/status/200")

    assert response.status_code == 200
    assert _pop_request_span(test_spans).service == "custom-http-client"


def test_split_by_domain(patched_niquests, test_spans):
    with override_config("niquests", {"split_by_domain": True}):
        response = niquests.get(HTTPBIN + "/status/200")

    assert response.status_code == 200
    assert _pop_request_span(test_spans).service == "localhost:8001"


def test_unpatch_disables_instrumentation(test_spans):
    patch()
    unpatch()
    response = niquests.get(HTTPBIN + "/status/200")

    assert response.status_code == 200
    assert test_spans.pop_traces() == []


def test_disabled_tracer_preserves_request(patched_niquests, tracer, test_spans):
    tracer.enabled = False
    try:
        response = niquests.get(HTTPBIN + "/status/200")
    finally:
        tracer.enabled = True

    assert response.status_code == 200
    assert test_spans.pop_traces() == []


def test_distributed_tracing_injects_request_span(patched_niquests, tracer, test_spans):
    with tracer.trace("parent") as parent:
        response = niquests.get(HTTPBIN + "/headers")

    headers = response.json()["headers"]
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 2
    request_span = next(span for span in traces[0] if span.name == "niquests.request")
    assert request_span.parent_id == parent.span_id
    assert request_span.trace_id == parent.trace_id
    assert headers["X-Datadog-Parent-Id"] == str(request_span.span_id)


def test_distributed_tracing_can_be_disabled(patched_niquests, tracer, test_spans):
    with override_config("niquests", {"distributed_tracing": False}):
        with tracer.trace("parent"):
            response = niquests.get(HTTPBIN + "/headers")

    headers = response.json()["headers"]
    traces = test_spans.pop_traces()
    assert len(traces) == 1
    assert len(traces[0]) == 2
    assert "X-Datadog-Trace-Id" not in headers
    assert "X-Datadog-Parent-Id" not in headers


def test_stream_span_finishes_after_body_consumption(patched_niquests, test_spans):
    response = niquests.get(HTTPBIN + "/stream/2", stream=True)
    assert test_spans.pop_traces() == []

    chunks = list(response.iter_content(chunk_size=16))

    assert chunks
    span = _pop_request_span(test_spans)
    _assert_client_span(span, "GET", "/stream/2", 200)


@pytest.mark.skipif(not hasattr(niquests, "AsyncSession"), reason="AsyncSession requires niquests>=3.14")
@pytest.mark.asyncio
async def test_async_success(patched_niquests, test_spans):
    async with niquests.AsyncSession() as session:
        response = await session.get(HTTPBIN + "/status/200")

    assert response.status_code == 200
    span = _pop_request_span(test_spans)
    _assert_client_span(span, "GET", "/status/200", 200)


@pytest.mark.skipif(not hasattr(niquests, "AsyncSession"), reason="AsyncSession requires niquests>=3.14")
@pytest.mark.asyncio
async def test_async_transport_error_preserves_exception(patched_niquests, test_spans):
    async with niquests.AsyncSession() as session:
        with pytest.raises(niquests.exceptions.ConnectionError) as raised:
            await session.get("http://127.0.0.1:1/unreachable", timeout=0.2)

    span = _pop_request_span(test_spans)
    assert span.error == 1
    assert span.get_tag(ERROR_TYPE).endswith("ConnectionError")
    assert span.get_tag(ERROR_MSG) == str(raised.value)


@pytest.mark.skipif(not hasattr(niquests, "AsyncSession"), reason="AsyncSession requires niquests>=3.14")
@pytest.mark.asyncio
async def test_async_stream_span_finishes_after_body_consumption(patched_niquests, test_spans):
    async with niquests.AsyncSession() as session:
        response = await session.get(HTTPBIN + "/stream/2", stream=True)
        assert test_spans.pop_traces() == []

        chunks = [chunk async for chunk in await response.iter_content(chunk_size=16)]

    assert chunks
    span = _pop_request_span(test_spans)
    _assert_client_span(span, "GET", "/stream/2", 200)


@pytest.mark.skipif(not hasattr(niquests.Session, "gather"), reason="multiplexing requires niquests>=3.2")
def test_multiplexed_span_finishes_when_response_is_gathered(patched_niquests, test_spans):
    with niquests.Session(multiplexed=True) as session:
        response = session.get(HTTPBIN + "/status/200")
        if not response.lazy:
            pytest.skip("the HTTPBin test service does not negotiate HTTP/2 or HTTP/3")
        assert test_spans.pop_traces() == []

        session.gather(response)

    assert response.status_code == 200
    span = _pop_request_span(test_spans)
    _assert_client_span(span, "GET", "/status/200", 200)
