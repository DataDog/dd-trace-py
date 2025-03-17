import asyncio
from functools import partial
import logging
import os
import random

from asgiref.testing import ApplicationCommunicator
import httpx
import pytest

from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import USER_KEEP
from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
from ddtrace.contrib.internal.asgi.middleware import _parse_response_cookies
from ddtrace.contrib.internal.asgi.middleware import span_from_scope
from ddtrace.propagation import http as http_propagation
from tests.conftest import DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
from tests.tracer.utils_inferred_spans.test_helpers import assert_web_and_inferred_aws_api_gateway_span_data
from tests.utils import DummyTracer
from tests.utils import override_global_config
from tests.utils import override_http_config


@pytest.fixture(
    params=[
        {},
        {"server": None},
        {"server": ("dev", 8000)},
        {"server": ("dev", None)},
        {"http_version": "1.0", "asgi": {}},
        {"http_version": "1.0", "asgi": {"version": "3.2"}},
        {
            "http_version": "1.0",
            "asgi": {
                "spec_version": "2.1",
            },
        },
        {
            "http_version": "1.0",
            "asgi": {
                "version": "3.2",
                "spec_version": "2.1",
            },
        },
    ]
)
def scope(request):
    s = {
        "client": ("127.0.0.1", 32767),
        "headers": [],
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "scheme": "http",
        "server": ("127.0.0.1", 80),
        "type": "http",
    }
    s.update(request.param)
    return {k: v for (k, v) in s.items() if v is not None}


@pytest.fixture
def tracer():
    tracer = DummyTracer()
    yield tracer


async def basic_app(scope, receive, send):
    assert scope["type"] == "http"
    message = await receive()
    if message.get("type") == "http.request":
        await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
        query_string = scope.get("query_string")
        if query_string and query_string == b"sleep=true":
            await asyncio.sleep(random.random())
            await send({"type": "http.response.body", "body": b"sleep"})
        else:
            await send({"type": "http.response.body", "body": b"*"})


async def error_app(scope, receive, send):
    raise RuntimeError("Test")


async def error_handled_app(scope, receive, send):
    message = await receive()
    if message.get("type") == "http.request":
        await send({"type": "http.response.start", "status": 500, "headers": [[b"Content-Type", b"text/plain"]]})
        await send({"type": "http.response.body", "body": b"*"})


def double_callable_app(scope):
    """
    A double-callable application for legacy ASGI 2.0 support
    """
    return partial(basic_app, scope)


async def tasks_app_without_more_body(scope, receive, send):
    """
    An app that does something in the background after the response is sent without having more data to send.
    "more_body" with a true value is used in the asgi spec to indicate that there is more data to send.
    """
    assert scope["type"] == "http"
    message = await receive()
    if message.get("type") == "http.request":
        await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
        await send({"type": "http.response.body", "body": b"*"})
        await asyncio.sleep(1)


async def tasks_app_with_more_body(scope, receive, send):
    """
    An app that does something in the background but has a more_body response that starts off as true,
    but then turns to false.
    "more_body" with a true value is used in the asgi spec to indicate that there is more data to send.
    """
    assert scope["type"] == "http"
    message = await receive()
    request_span = scope["datadog"]["request_spans"][0]
    if message.get("type") == "http.request":
        # assert that the request span hasn't finished at the start of a response
        await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
        assert not request_span.finished

        # assert that the request span hasn't finished while more_body is True
        await send({"type": "http.response.body", "body": b"*", "more_body": True})
        assert not request_span.finished

        # assert that the span has finished after more_body is False
        await send({"type": "http.response.body", "body": b"*", "more_body": False})
        assert request_span.finished
        await asyncio.sleep(1)


def _check_span_tags(scope, span):
    assert span.get_tag("http.method") == scope["method"]
    server = scope.get("server")
    expected_http_url = (
        "http://{}{}".format(server[0], ":{}/".format(server[1]) if server[1] != 80 else "/") if server else None
    )
    assert expected_http_url or span.get_tag("http.url") == expected_http_url
    assert (
        scope.get("query_string") is None
        or scope.get("query_string") == b""
        or span.get_tag("http.query.string") == scope["query_string"]
    )
    assert scope.get("http_version") is None or span.get_tag("http.version") == scope["http_version"]
    assert (
        scope.get("asgi") is None
        or scope["asgi"].get("version") is None
        or span.get_tag("asgi.version") == scope["asgi"]["version"]
    )
    assert (
        scope.get("asgi") is None
        or scope["asgi"].get("spec_version") is None
        or span.get_tag("asgi.spec_version") == scope["asgi"]["spec_version"]
    )


@pytest.mark.asyncio
async def test_basic_asgi(scope, tracer, test_spans):
    app = TraceMiddleware(basic_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({"type": "http.request", "body": b""})
    response_start = await instance.receive_output(1)
    assert response_start == {
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }
    response_body = await instance.receive_output(1)
    assert response_body == {
        "type": "http.response.body",
        "body": b"*",
    }

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    assert request_span.error == 0
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "asgi"
    assert request_span.get_tag("span.kind") == "server"
    _check_span_tags(scope, request_span)


@pytest.mark.parametrize("schema_version", [None, "v0", "v1"])
def test_span_attribute_schema_operation_name(ddtrace_run_python_code_in_subprocess, schema_version):
    expected_span_name = {None: "asgi.request", "v0": "asgi.request", "v1": "http.server.request"}[schema_version]
    code = """
import pytest
from tests.conftest import *
from tests.contrib.asgi.test_asgi import basic_app
from tests.contrib.asgi.test_asgi import scope
from tests.contrib.asgi.test_asgi import tracer
from asgiref.testing import ApplicationCommunicator
from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
from ddtrace.contrib.internal.asgi.middleware import span_from_scope


@pytest.mark.asyncio
async def test(scope, tracer, test_spans):
    app = TraceMiddleware(basic_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({{"type": "http.request", "body": b""}})
    response_start = await instance.receive_output(1)
    assert response_start == {{
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }}
    response_body = await instance.receive_output(1)
    assert response_body == {{
        "type": "http.response.body",
        "body": b"*",
    }}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "{}"

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_span_name
    )
    env = os.environ.copy()
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err, out)
    assert err == b"", f"STDOUT\n{out.decode()}\nSTDERR\n{err.decode()}"


@pytest.mark.parametrize(
    "schema_version, global_service_name",
    [(None, None), (None, "mysvc"), ("v0", None), ("v0", "mysvc"), ("v1", None), ("v1", "mysvc")],
)
def test_span_attribute_schema_service_name(ddtrace_run_python_code_in_subprocess, schema_version, global_service_name):
    inferred_base_service = DEFAULT_DDTRACE_SUBPROCESS_TEST_SERVICE_NAME
    expected_service_name = {
        None: global_service_name or inferred_base_service,
        "v0": global_service_name or inferred_base_service,
        "v1": global_service_name or inferred_base_service,
    }[schema_version]
    code = """
import pytest
from tests.conftest import *
from tests.contrib.asgi.test_asgi import basic_app
from tests.contrib.asgi.test_asgi import scope
from tests.contrib.asgi.test_asgi import tracer
from asgiref.testing import ApplicationCommunicator
from ddtrace.contrib.internal.asgi.middleware import TraceMiddleware
from ddtrace.contrib.internal.asgi.middleware import span_from_scope

@pytest.mark.asyncio
async def test(scope, tracer, test_spans):
    app = TraceMiddleware(basic_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({{"type": "http.request", "body": b""}})
    response_start = await instance.receive_output(1)
    assert response_start == {{
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }}
    response_body = await instance.receive_output(1)
    assert response_body == {{
        "type": "http.response.body",
        "body": b"*",
    }}

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    service = "{}"
    assert request_span.service == (service or None)

if __name__ == "__main__":
    import sys
    sys.exit(pytest.main(["-x", __file__]))
    """.format(
        expected_service_name
    )
    env = os.environ.copy()
    if global_service_name:
        env["DD_SERVICE"] = global_service_name
    if schema_version:
        env["DD_TRACE_SPAN_ATTRIBUTE_SCHEMA"] = schema_version
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err, out)
    assert err == b""


@pytest.mark.asyncio
async def test_double_callable_asgi(scope, tracer, test_spans):
    app = TraceMiddleware(double_callable_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({"type": "http.request", "body": b""})
    response_start = await instance.receive_output(1)
    assert response_start == {
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }
    response_body = await instance.receive_output(1)
    assert response_body == {
        "type": "http.response.body",
        "body": b"*",
    }

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    assert request_span.error == 0
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "asgi"
    assert request_span.get_tag("span.kind") == "server"
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_query_string(scope, tracer, test_spans):
    with override_http_config("asgi", dict(trace_query_string=True)):
        app = TraceMiddleware(basic_app, tracer=tracer)
        scope["query_string"] = "foo=bar"
        instance = ApplicationCommunicator(app, scope)
        await instance.send_input({"type": "http.request", "body": b""})
        response_start = await instance.receive_output(1)
        assert response_start == {
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"Content-Type", b"text/plain"]],
        }
        response_body = await instance.receive_output(1)
        assert response_body == {
            "type": "http.response.body",
            "body": b"*",
        }

        spans = test_spans.pop_traces()
        assert len(spans) == 1
        assert len(spans[0]) == 1
        request_span = spans[0][0]
        assert request_span.name == "asgi.request"
        assert request_span.span_type == "web"
        assert request_span.error == 0
        assert request_span.get_tag("http.status_code") == "200"
        assert request_span.get_tag("component") == "asgi"
        assert request_span.get_tag("span.kind") == "server"
        _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_asgi_error(scope, tracer, test_spans):
    app = TraceMiddleware(error_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)
    with pytest.raises(RuntimeError):
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    assert request_span.error == 1
    assert request_span.get_tag("http.status_code") == "500"
    assert request_span.get_tag(ERROR_MSG) == "Test"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert request_span.get_tag("component") == "asgi"
    assert request_span.get_tag("span.kind") == "server"
    assert 'raise RuntimeError("Test")' in request_span.get_tag("error.stack")
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_asgi_500(scope, tracer, test_spans):
    app = TraceMiddleware(error_handled_app, tracer=tracer)
    instance = ApplicationCommunicator(app, scope)

    await instance.send_input({"type": "http.request", "body": b""})
    await instance.receive_output(1)

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    assert request_span.error == 1
    assert request_span.get_tag("http.status_code") == "500"
    assert request_span.get_tag("component") == "asgi"
    assert request_span.get_tag("span.kind") == "server"


@pytest.mark.asyncio
async def test_asgi_error_custom(scope, tracer, test_spans):
    def custom_handle_exception_span(exc, span):
        span.set_tag("http.status_code", 501)

    app = TraceMiddleware(error_app, tracer=tracer, handle_exception_span=custom_handle_exception_span)
    instance = ApplicationCommunicator(app, scope)
    with pytest.raises(RuntimeError):
        await instance.send_input({"type": "http.request", "body": b""})
        await instance.receive_output(1)

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    assert request_span.error == 1
    assert request_span.get_tag("http.status_code") == "501"
    assert request_span.get_tag(ERROR_MSG) == "Test"
    assert request_span.get_tag("error.type") == "builtins.RuntimeError"
    assert request_span.get_tag("component") == "asgi"
    assert request_span.get_tag("span.kind") == "server"
    assert 'raise RuntimeError("Test")' in request_span.get_tag("error.stack")
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_distributed_tracing(scope, tracer, test_spans):
    app = TraceMiddleware(basic_app, tracer=tracer)
    headers = [
        (http_propagation.HTTP_HEADER_PARENT_ID.encode(), "1234".encode()),
        (http_propagation.HTTP_HEADER_TRACE_ID.encode(), "5678".encode()),
    ]
    scope["headers"] = headers
    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({"type": "http.request", "body": b""})
    response_start = await instance.receive_output(1)
    assert response_start == {
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }
    response_body = await instance.receive_output(1)
    assert response_body == {
        "type": "http.response.body",
        "body": b"*",
    }

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    assert request_span.parent_id == 1234
    assert request_span.trace_id == 5678
    assert request_span.error == 0
    assert request_span.get_tag("http.status_code") == "200"
    assert request_span.get_tag("component") == "asgi"
    assert request_span.get_tag("span.kind") == "server"
    _check_span_tags(scope, request_span)


@pytest.mark.asyncio
async def test_multiple_requests(tracer, test_spans):
    with override_http_config("asgi", dict(trace_query_string=True)):
        app = TraceMiddleware(basic_app, tracer=tracer)
        async with httpx.AsyncClient(app=app) as client:
            responses = await asyncio.gather(
                client.get("http://testserver/", params={"sleep": True}),
                client.get("http://testserver/", params={"sleep": True}),
            )

    assert len(responses) == 2
    assert [r.status_code for r in responses] == [200] * 2
    assert [r.text for r in responses] == ["sleep"] * 2

    spans = test_spans.pop_traces()
    assert len(spans) == 2
    assert len(spans[0]) == 1
    assert len(spans[1]) == 1

    r1_span = spans[0][0]
    assert r1_span.name == "asgi.request"
    assert r1_span.span_type == "web"
    assert r1_span.get_tag("http.method") == "GET"
    assert r1_span.get_tag("http.url") == "http://testserver/?sleep=true"
    assert r1_span.get_tag("http.query.string") == "sleep=true"
    assert r1_span.get_tag("component") == "asgi"
    assert r1_span.get_tag("span.kind") == "server"

    r2_span = spans[0][0]
    assert r2_span.name == "asgi.request"
    assert r2_span.span_type == "web"
    assert r2_span.get_tag("http.method") == "GET"
    assert r2_span.get_tag("http.url") == "http://testserver/?sleep=true"
    assert r2_span.get_tag("http.query.string") == "sleep=true"
    assert r2_span.get_tag("component") == "asgi"
    assert r2_span.get_tag("span.kind") == "server"


@pytest.mark.asyncio
async def test_bad_headers(scope, tracer, test_spans):
    """
    When headers can't be decoded
        The middleware should not raise
    """

    app = TraceMiddleware(basic_app, tracer=tracer)
    headers = [(bytes.fromhex("c0"), "test")]
    scope["headers"] = headers

    instance = ApplicationCommunicator(app, scope)
    await instance.send_input({"type": "http.request", "body": b""})
    response_start = await instance.receive_output(1)
    assert response_start == {
        "type": "http.response.start",
        "status": 200,
        "headers": [[b"Content-Type", b"text/plain"]],
    }
    response_body = await instance.receive_output(1)
    assert response_body == {
        "type": "http.response.body",
        "body": b"*",
    }


@pytest.mark.asyncio
async def test_get_asgi_span(tracer, test_spans):
    async def test_app(scope, receive, send):
        message = await receive()
        if message.get("type") == "http.request":
            asgi_span = span_from_scope(scope)
            assert asgi_span is not None
            assert asgi_span.name == "asgi.request"
            await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
            await send({"type": "http.response.body", "body": b""})

    app = TraceMiddleware(test_app, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        response = await client.get("http://testserver/")
        assert response.status_code == 200

    with override_http_config("asgi", dict(trace_query_string=True)):
        app = TraceMiddleware(test_app, tracer=tracer)
        async with httpx.AsyncClient(app=app) as client:
            response = await client.get("http://testserver/")
            assert response.status_code == 200

    async def test_app_that_generates_multiple_request_spans(scope, receive, send):
        message = await receive()
        if message.get("type") == "http.request":
            assert len(scope["datadog"]["request_spans"]) == 2
            asgi_span = span_from_scope(scope)
            assert asgi_span is not None
            assert asgi_span.name == "asgi.request"
            assert asgi_span == scope["datadog"]["request_spans"][0]
            assert asgi_span.resource == "span 1 resource"
            assert scope["datadog"]["request_spans"][1].resource == "span 2 resource"
            await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
            await send({"type": "http.response.body", "body": b""})

    def span1_modifier(span, scope):
        span.resource = "span 1 resource"

    def span2_modifier(span, scope):
        span.resource = "span 2 resource"

    # We double wrap this app so that it generates multiple request spans per hit
    app = TraceMiddleware(
        TraceMiddleware(test_app_that_generates_multiple_request_spans, tracer=tracer, span_modifier=span2_modifier),
        tracer=tracer,
        span_modifier=span1_modifier,
    )

    async with httpx.AsyncClient(app=app) as client:
        response = await client.get("http://testserver/")
        assert response.status_code == 200

    async def test_app(scope, receive, send):
        message = await receive()
        if message.get("type") == "http.request":
            root = tracer.current_root_span()
            assert root.name == "root"
            asgi_span = span_from_scope(scope)
            assert asgi_span is not None
            assert asgi_span.name == "asgi.request"
            await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
            await send({"type": "http.response.body", "body": b""})

    app = TraceMiddleware(test_app, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        with tracer.trace("root"):
            response = await client.get("http://testserver/")
            assert response.status_code == 200

    async def test_app_no_middleware(scope, receive, send):
        message = await receive()
        if message.get("type") == "http.request":
            asgi_span = span_from_scope(scope)
            assert asgi_span is None
            await send({"type": "http.response.start", "status": 200, "headers": [[b"Content-Type", b"text/plain"]]})
            await send({"type": "http.response.body", "body": b""})

    async with httpx.AsyncClient(app=test_app_no_middleware) as client:
        response = await client.get("http://testserver/")
        assert response.status_code == 200


@pytest.mark.asyncio
async def test_tasks_asgi_without_more_body(scope, tracer, test_spans):
    """
    When an application doesn't have more_body calls and does background tasks,
    the asgi span only captures the time it took for a user to get a response, not the time
    it took for other tasks in the background.
    """
    app = TraceMiddleware(tasks_app_without_more_body, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        response = await client.get("http://testserver/")
        assert response.status_code == 200

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    # typical duration without background task should be in less than 10 ms
    # duration with background task will take approximately 1.1s
    assert request_span.duration < 1


@pytest.mark.asyncio
async def test_request_parse_response_cookies(tracer, test_spans, caplog):
    """
    Regression test https://github.com/DataDog/dd-trace-py/issues/11818
    """

    async def tasks_cookies(scope, receive, send):
        message = await receive()
        if message.get("type") == "http.request":
            await send({"type": "http.response.start", "status": 200, "headers": [[b"set-cookie", b"test_cookie"]]})
            await send({"type": "http.response.body", "body": b"*"})
            await asyncio.sleep(1)

    with caplog.at_level(logging.DEBUG):
        app = TraceMiddleware(tasks_cookies, tracer=tracer)
        async with httpx.AsyncClient(app=app) as client:
            response = await client.get("http://testserver/")
            assert response.status_code == 200

    assert "failed to extract response cookies" not in caplog.text


@pytest.mark.parametrize(
    "headers,expected_result",
    [
        ({}, {}),
        ({"cookie": "cookie1=value1"}, {}),
        ({"header-1": ""}, {}),
        ({"Set-cookie": "cookie1=value1"}, {}),
        ({"set-Cookie": "cookie1=value1"}, {}),
        ({"SET-cookie": "cookie1=value1"}, {}),
        ({"set-cookie": "a"}, {}),
        ({"set-cookie": "1234"}, {}),
        ({"set-cookie": "cookie1=value1"}, {"cookie1": "value1"}),
        ({"set-cookie": "cookie2=value1=value2"}, {"cookie2": "value1=value2"}),
        ({"set-cookie": "cookie3=="}, {"cookie3": "="}),
    ],
)
def test__parse_response_cookies(headers, expected_result, caplog):
    with caplog.at_level(logging.DEBUG):
        result = _parse_response_cookies(headers)

    assert "failed to extract response cookies" not in caplog.text
    assert result == expected_result


@pytest.mark.asyncio
async def test_tasks_asgi_with_more_body(scope, tracer, test_spans):
    """
    When an application does have more_body calls and does background tasks,
    the asgi span only captures the time it took for a user to get a response, not the time
    it took for other tasks in the background.
    """
    app = TraceMiddleware(tasks_app_with_more_body, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        response = await client.get("http://testserver/")
        assert response.status_code == 200

    spans = test_spans.pop_traces()
    assert len(spans) == 1
    assert len(spans[0]) == 1
    request_span = spans[0][0]
    assert request_span.name == "asgi.request"
    assert request_span.span_type == "web"
    # typical duration without background task should be in less than 10 ms
    # duration with background task will take approximately 1.1s
    assert request_span.duration < 1


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["hostserver", "hostserver:5454"])
async def test_host_header(scope, tracer, test_spans, host):
    app = TraceMiddleware(basic_app, tracer=tracer)
    async with httpx.AsyncClient(app=app) as client:
        response = await client.get("http://testserver/", headers={"host": host})
        assert response.status_code == 200

        assert test_spans.spans
        request_span = test_spans.spans[0]
        assert request_span.get_tag("http.url") == "http://%s/" % (host,)


@pytest.mark.asyncio
async def test_response_headers(scope, tracer, test_spans):
    with override_http_config("asgi", {"trace_headers": ["content-type"]}):
        app = TraceMiddleware(basic_app, tracer=tracer)
        async with httpx.AsyncClient(app=app) as client:
            response = await client.get("http://testserver/")
            assert response.status_code == 200

            assert test_spans.spans
            request_span = test_spans.spans[0]
            assert "http.response.headers.content-type" in request_span.get_tags().keys()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "app_type",
    [
        {"testapp": basic_app, "status_code": "200"},
        {"testapp": error_app, "status_code": "500"},
        {"testapp": error_handled_app, "status_code": "500"},
    ],
)
@pytest.mark.parametrize("inferred_proxy_enabled", [False, True])
async def test_inferred_spans_api_gateway_default(scope, tracer, test_spans, app_type, inferred_proxy_enabled):
    app = TraceMiddleware(app_type["testapp"], tracer=tracer)
    default_headers = [
        ("x-dd-proxy", "aws-apigateway"),
        ("x-dd-proxy-request-time-ms", "1736973768000"),
        ("x-dd-proxy-path", "/"),
        ("x-dd-proxy-httpmethod", "GET"),
        ("x-dd-proxy-domain-name", "local"),
        ("x-dd-proxy-stage", "stage"),
    ]

    distributed_headers = [
        ("x-dd-proxy", "aws-apigateway"),
        ("x-dd-proxy-request-time-ms", "1736973768000"),
        ("x-dd-proxy-path", "/"),
        ("x-dd-proxy-httpmethod", "GET"),
        ("x-dd-proxy-domain-name", "local"),
        ("x-dd-proxy-stage", "stage"),
        ("x-datadog-trace-id", "1"),
        ("x-datadog-parent-id", "2"),
        ("x-datadog-origin", "rum"),
        ("x-datadog-sampling-priority", "2"),
    ]

    for headers in [default_headers, distributed_headers]:
        scope["headers"] = headers

        # When the inferred proxy feature is enabled, there should be an inferred span
        with override_global_config(dict(_inferred_proxy_services_enabled=inferred_proxy_enabled)):
            test_spans.reset()
            instance = ApplicationCommunicator(app, scope)

            if app_type["testapp"] == error_app:
                with pytest.raises(RuntimeError):
                    await instance.send_input({"type": "http.request", "body": b""})
                    await instance.receive_output(1)
            else:
                await instance.send_input({"type": "http.request", "body": b""})
                await instance.receive_output(1)

            web_span = test_spans.find_span(name="asgi.request")
            if inferred_proxy_enabled is False:
                assert web_span._parent is None

                if headers == distributed_headers:
                    web_span.assert_matches(
                        name="asgi.request",
                        trace_id=1,
                        parent_id=2,
                        metrics={
                            _SAMPLING_PRIORITY_KEY: USER_KEEP,
                        },
                    )
            else:
                aws_gateway_span = test_spans.find_span(name="aws.apigateway")

                assert_web_and_inferred_aws_api_gateway_span_data(
                    aws_gateway_span,
                    web_span,
                    web_span_name="asgi.request",
                    web_span_component="asgi",
                    web_span_service_name="tests.contrib.asgi",
                    web_span_resource="GET /",
                    api_gateway_service_name="local",
                    api_gateway_resource="GET /",
                    method="GET",
                    status_code=app_type["status_code"],
                    url="local/",
                    start=1736973768,
                    is_distributed=headers == distributed_headers,
                    distributed_trace_id=1,
                    distributed_parent_id=2,
                    distributed_sampling_priority=USER_KEEP,
                )
