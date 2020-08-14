import pytest
from webtest import TestApp

from ddtrace.vendor import six

from ddtrace.compat import PY2, PY3
from ddtrace.contrib.wsgi import wsgi

from tests import snapshot


def chunked_response():
    for i in range(1000):
        yield "%d" % i


def application(environ, start_response):
    if environ["PATH_INFO"] == "/error":
        raise Exception("Oops!")
    elif environ["PATH_INFO"] == "/chunked":
        return chunked_response()
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
