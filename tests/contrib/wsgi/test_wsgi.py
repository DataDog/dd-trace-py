import pytest
import six
from webtest import TestApp

from ddtrace import config
from ddtrace.contrib.wsgi import wsgi
from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import PY3
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
            raise GeneratorExit()


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


class WsgiCustomMiddleware(wsgi._DDWSGIMiddlewareBase):
    _request_span_name = "test_wsgi.request"
    _application_span_name = "test_wsgi.application"
    _response_span_name = "test_wsgi.response"

    def _request_span_modifier(self, req_span, environ):
        req_span.set_tag("request_tag", "req test tag set")
        req_span.set_metric("request_metric", 1)
        req_span.resource = "request resource was modified"

    def _application_span_modifier(self, app_span, environ, result):
        app_span.set_tag("app_tag", "app test tag set")
        app_span.set_metric("app_metric", 2)
        app_span.resource = "app resource was modified"

    def _response_span_modifier(self, resp_span, response):
        resp_span.set_tag("response_tag", "resp test tag set")
        resp_span.set_metric("response_metric", 3)
        resp_span.resource = "response resource was modified"


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
    try:
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        resp = app.get("/", headers={"my-header": "test_value"})

        assert resp.status == "200 OK"
        assert resp.status_int == 200
        spans = test_spans.pop()
        assert spans[0].get_tag("http.request.headers.my-header") == "test_value"
    finally:
        config.wsgi.http._reset()


def test_http_response_header_tracing(tracer, test_spans):
    config.wsgi.http.trace_headers(["my-response-header"])
    try:
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        resp = app.get("/", headers={"my-header": "test_value"})

        assert resp.status == "200 OK"
        assert resp.status_int == 200

        spans = test_spans.pop()
        assert spans[0].get_tag("http.response.headers.my-response-header") == "test_response_value"
    finally:
        config.wsgi.http._reset()


def test_service_name_can_be_overriden(tracer, test_spans):
    with override_config("wsgi", dict(service_name="test-override-service")):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        response = app.get("/")
        assert response.status_code == 200

        spans = test_spans.pop_traces()
        assert len(spans) > 0
        span = spans[0][0]
        assert span.service == "test-override-service"


def test_generator_exit_ignored(tracer, test_spans):
    with pytest.raises(GeneratorExit):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        app.get("/generatorError")

    spans = test_spans.pop()
    assert spans[2].name == "wsgi.response"
    assert spans[2].error == 0
    assert spans[0].name == "wsgi.request"
    assert spans[0].error == 0


@snapshot()
def test_generator_exit_ignored_snapshot():
    with pytest.raises(GeneratorExit):
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


def test_chunked_response_custom_middleware(tracer, test_spans):
    app = TestApp(WsgiCustomMiddleware(application, tracer, config.wsgi, None))
    resp = app.get("/chunked")
    assert resp.status == "200 OK"
    assert resp.status_int == 200
    assert resp.text.startswith("0123456789")
    assert resp.text.endswith("999")

    traces = test_spans.pop_traces()
    assert len(traces) == 1

    spans = traces[0]
    assert len(spans) == 3
    assert spans[0].name == "test_wsgi.request"
    assert spans[1].name == "test_wsgi.application"
    assert spans[2].name == "test_wsgi.response"


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


@pytest.mark.snapshot(token="tests.contrib.wsgi.test_wsgi.test_wsgi_base_middleware")
@pytest.mark.parametrize("use_global_tracer", [True])
def test_wsgi_base_middleware(use_global_tracer, tracer):
    app = TestApp(WsgiCustomMiddleware(application, tracer, config.wsgi, None))
    resp = app.get("/")
    assert resp.status == "200 OK"
    assert resp.status_int == 200


@pytest.mark.snapshot(
    token="tests.contrib.wsgi.test_wsgi.test_wsgi_base_middleware_500", ignores=["meta.error.stack", "meta.error.type"]
)
@pytest.mark.parametrize("use_global_tracer", [True])
def test_wsgi_base_middleware_500(use_global_tracer, tracer):
    # Note - span modifiers are not called
    app = TestApp(WsgiCustomMiddleware(application, tracer, config.wsgi, None))
    with pytest.raises(Exception):
        app.get("/error")


@pytest.mark.snapshot(ignores=["meta.result_class"])
def test_distributed_tracing_nested():
    app = TestApp(
        wsgi.DDWSGIMiddleware(
            wsgi.DDWSGIMiddleware(application),
        )
    )
    # meta.result_class is listiterator in PY2 and list_iterator in PY3. Ignore this field to
    # simplify this test. Otherwise we'd need different snapshots for PY2 and PY3.
    resp = app.get("/", headers={"X-Datadog-Parent-Id": "1234", "X-Datadog-Trace-Id": "4321"})

    assert config.wsgi.distributed_tracing is True
    assert resp.status == "200 OK"
    assert resp.status_int == 200
