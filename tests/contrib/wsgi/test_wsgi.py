import pytest
from webtest import TestApp

from ddtrace.vendor import six

from ddtrace.compat import PY2, PY3
from ddtrace.contrib.wsgi import wsgi

from tests import snapshot, override_config


def chunked_response(start_response):
    status = '200 OK'
    headers = [('Content-type', 'text/plain')]
    start_response(status, headers)
    for i in range(1000):
        yield b"%d" % i


def application(environ, start_response):
    if environ["PATH_INFO"] == "/error":
        raise Exception("Oops!")
    elif environ["PATH_INFO"] == "/chunked":
        return chunked_response(start_response)
    else:
        body = six.b("<html><body><h1>Hello World</h1></body></html>")
        headers = [("Content-Type", "text/html; charset=utf8"), ("Content-Length", str(len(body)))]
        start_response("200 OK", headers)
        return [body]


def test_middleware(tracer):
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/")
    assert resp.status == "200 OK"
    assert resp.status_int == 200
    spans = tracer.writer.pop()
    assert len(spans) == 4

    with pytest.raises(Exception):
        app.get("/error")

    spans = tracer.writer.pop()
    assert len(spans) == 2
    assert spans[0].error == 1


def test_distributed_tracing(tracer):
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/", headers={"X-Datadog-Parent-Id": "1234", "X-Datadog-Trace-Id": "4321"})
    assert resp.status == "200 OK"
    assert resp.status_int == 200

    spans = tracer.writer.pop()
    assert len(spans) == 4
    root = spans[0]
    assert root.name == "wsgi.request"
    assert root.trace_id == 4321
    assert root.parent_id == 1234


def test_service_name_can_be_overriden(tracer):
    with override_config("wsgi", dict(service_name='test-override-service')):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        response = app.get("/")
        assert response.status_code == 200

        spans = tracer.writer.pop_traces()
        assert len(spans) > 0
        span = spans[0][0]
        assert span.service == "test-override-service"


def test_chunked_response(tracer):
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/chunked")
    assert resp.status == "200 OK"
    assert resp.status_int == 200
    assert resp.text.startswith("0123456789")
    assert resp.text.endswith("999")

    spans = tracer.writer.pop_traces()
    span = spans[0][0]
    assert span.resource == "GET /chunked"
    assert span.name == "wsgi.request"


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


