from webtest import TestApp
import pytest

from ddtrace.vendor import six
from ddtrace.contrib.wsgi import wsgi

from tests import override_global_config

"""
TODO
- tracing tests
- test when encoding check fails -> ensure content-length is correct
"""


def application(environ, start_response):
    if environ["PATH_INFO"] == "/error":
        raise Exception("Oops!")
    else:
        body = six.b("<html><body><h1>Hello World</h1></body></html>")
        headers = [("Content-Type", "text/html; charset=utf8"), ("Content-Length", str(len(body)))]
        start_response("200 OK", headers)
        return [body]


def test_parse_content_type():
    assert wsgi.parse_content_type("text/html") == ("text/html", None, None)
    assert wsgi.parse_content_type("text/html; charset=UTF-8") == ("text/html", "utf-8", None)
    assert wsgi.parse_content_type("multipart/form-data; boundary=something") == (
        "multipart/form-data",
        None,
        "something",
    )
    assert wsgi.parse_content_type("multipart/form-data; boundary=something; charset=UTF-8") == (
        "multipart/form-data",
        "utf-8",
        "something",
    )


def test_middleware(tracer):
    app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
    resp = app.get("/")
    assert resp.status == "200 OK"
    assert resp.status_int == 200
    spans = tracer.writer.pop()
    assert len(spans) == 2

    with pytest.raises(Exception):
        app.get("/error")

    spans = tracer.writer.pop()
    assert len(spans) == 1


def test_middleware_rum(tracer):
    with override_global_config(dict(rum_header_injection=True)):
        app = TestApp(wsgi.DDWSGIMiddleware(application, tracer=tracer))
        resp = app.get("/")

    assert resp.status == "200 OK"
    assert resp.status_int == 200
    assert b"DATADOG" in resp.body
    assert len(resp.body) == resp.content_length

