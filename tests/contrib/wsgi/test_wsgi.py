import pytest
from webtest import TestApp

from ddtrace import config
from ddtrace.compat import PY2
from ddtrace.compat import PY3
from ddtrace.contrib.wsgi import wsgi
from ddtrace.vendor import six


if PY2:
    import exceptions

    generatorExit = exceptions.GeneratorExit
else:
    import builtins

    generatorExit = builtins.GeneratorExit

from tests.utils import override_config
from tests.utils import override_http_config
from tests.utils import snapshot


def chunked_response(start_response):
    status = "200 OK"
    headers = [("Content-type", "text/plain")]
    start_response(status, headers)
    for i in range(1000):
        yield b"%d" % i


def chunked_response_generator_error(start_response):
    status = "200 OK"
    headers = [("Content-type", "text/plain")]
    start_response(status, headers)
    for i in range(1000):
        if i < 999:
            yield b"%d" % i
        else:
            raise generatorExit()


def application(environ, start_response):
    if environ["PATH_INFO"] == "/error":
        raise Exception("Oops!")
    elif environ["PATH_INFO"] == "/chunked":
        return chunked_response(start_response)
    elif environ["PATH_INFO"] == "/generatorError":
        return chunked_response_generator_error(start_response)
    else:
        body = six.b("<html><body><h1>Hello World</h1></body></html>")
        headers = [
            ("Content-Type", "text/html; charset=utf8"),
            ("Content-Length", str(len(body))),
            ("my-response-header", "test_response_value"),
        ]
        start_response("200 OK", headers)
        return [body]


def test_middleware(tracer, test_spans):
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/")
    assert resp.status == "200 OK"
    assert resp.status_int == 200
    spans = test_spans.pop()
    assert len(spans) == 4

    with pytest.raises(Exception):
        app.get("/error")

    spans = test_spans.pop()
    assert len(spans) == 2
    assert spans[0].error == 1


def test_distributed_tracing(tracer, test_spans):
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/", headers={"X-Datadog-Parent-Id": "1234", "X-Datadog-Trace-Id": "4321"})

    assert config.wsgi.distributed_tracing is True
    assert resp.status == "200 OK"
    assert resp.status_int == 200

    spans = test_spans.pop()
    assert len(spans) == 4
    root = spans[0]
    assert root.name == "wsgi.request"
    assert root.trace_id == 4321
    assert root.parent_id == 1234

    with override_config("wsgi", dict(distributed_tracing=False)):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        resp = app.get("/", headers={"X-Datadog-Parent-Id": "1234", "X-Datadog-Trace-Id": "4321"})
        assert config.wsgi.distributed_tracing is False
        assert resp.status == "200 OK"
        assert resp.status_int == 200

        spans = test_spans.pop()
        assert len(spans) == 4
        root = spans[0]
        assert root.name == "wsgi.request"
        assert root.trace_id != 4321
        assert root.parent_id != 1234


def test_query_string_tracing(tracer, test_spans):
    with override_http_config("wsgi", dict(trace_query_string=True)):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        response = app.get("/?foo=bar&x=y")

        assert response.status_int == 200
        assert response.status == "200 OK"

        spans = test_spans.pop_traces()
        assert len(spans) == 1
        assert len(spans[0]) == 4
        request_span = spans[0][0]
        assert request_span.service == "wsgi"
        assert request_span.name == "wsgi.request"
        assert request_span.resource == "GET /"
        assert request_span.error == 0
        assert request_span.get_tag("http.method") == "GET"
        assert request_span.get_tag("http.status_code") == "200"
        assert request_span.get_tag("http.query.string") == "foo=bar&x=y"

        assert spans[0][1].name == "wsgi.application"
        assert spans[0][2].name == "wsgi.start_response"
        assert spans[0][3].name == "wsgi.response"


def test_http_request_header_tracing(tracer, test_spans):
    config.wsgi.http.trace_headers(["my-header"])
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/", headers={"my-header": "test_value"})

    assert resp.status == "200 OK"
    assert resp.status_int == 200
    spans = test_spans.pop()
    assert spans[0].get_tag("http.request.headers.my-header") == "test_value"


def test_http_response_header_tracing(tracer, test_spans):
    config.wsgi.http.trace_headers(["my-response-header"])
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/", headers={"my-header": "test_value"})

    assert resp.status == "200 OK"
    assert resp.status_int == 200

    spans = test_spans.pop()
    assert spans[0].get_tag("http.response.headers.my-response-header") == "test_response_value"


def test_service_name_can_be_overriden(tracer, test_spans):
    with override_config("wsgi", dict(service_name="test-override-service")):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        response = app.get("/")
        assert response.status_code == 200

        spans = test_spans.pop_traces()
        assert len(spans) > 0
        span = spans[0][0]
        assert span.service == "test-override-service"


def test_generator_exit_ignored_in_top_level_span(tracer, test_spans):
    with pytest.raises(generatorExit):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        app.get("/generatorError")

    spans = test_spans.pop()
    assert spans[2].error == 1
    assert "GeneratorExit" in spans[2].get_tag("error.type")
    assert spans[0].error == 0


@snapshot(ignores=["meta.error.stack"], variants={"py2": PY2, "py3": PY3})
def test_generator_exit_ignored_in_top_level_span_snapshot():
    with pytest.raises(generatorExit):
        app = TestApp(wsgi.DDWSGIMiddleware(application))
        app.get("/generatorError")


def test_chunked_response(tracer, test_spans):
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/chunked")
    assert resp.status == "200 OK"
    assert resp.status_int == 200
    assert resp.text.startswith("0123456789")
    assert resp.text.endswith("999")

    spans = test_spans.pop_traces()
    span = spans[0][0]
    assert span.resource == "GET /chunked"
    assert span.name == "wsgi.request"


@snapshot()
def test_chunked():
    app = TestApp(wsgi.DDWSGIMiddleware(application))
    resp = app.get("/chunked")
    assert resp.status == "200 OK"
    assert resp.status_int == 200
    assert resp.text.startswith("0123456789")
    assert resp.text.endswith("999")


@snapshot()
def test_200():
    app = TestApp(wsgi.DDWSGIMiddleware(application))
    resp = app.get("/")
    assert resp.status == "200 OK"
    assert resp.status_int == 200


@snapshot(ignores=["meta.error.stack"], variants={"py2": PY2, "py3": PY3})
def test_500():
    app = TestApp(wsgi.DDWSGIMiddleware(application))
    with pytest.raises(Exception):
        app.get("/error")
