
import responder

from ddtrace.contrib.responder import TraceMiddleware
from ...utils.tracer import DummyTracer


class TestResponder(object):

    def test_200(self):
        tracer, api = _make_test_api()
        resp = api.session().get("/login")

        assert resp.ok
        assert resp.status_code == 200

        spans = tracer.writer.pop()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == 'responder.request'
        assert s.get_tag('http.status_code') == '200'
        assert s.get_tag('http.method') == 'GET'


    def test_exception(self):
        tracer, api = _make_test_api()

        # don't raise exceptions so we can test status codes, etc.
        client = responder.api.TestClient(api, raise_server_exceptions=False)

        resp = client.get("/exception")

        assert not resp.ok
        assert resp.status_code == 500

        spans = tracer.writer.pop()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == 'responder.request'
        assert s.get_tag('http.status_code') == '500'
        assert s.get_tag('http.method') == 'GET'


    def test_tracing_http_headers(self):
        tracer, api = _make_test_api()
        resp = api.session().get("/login", headers={
            "x-datadog-trace-id":"123",
            "x-datadog-parent-id":"456",
        })

        assert resp.ok
        assert resp.status_code == 200

        spans = tracer.writer.pop()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == 'responder.request'
        assert s.trace_id == 123
        assert s.parent_id == 456


def _make_test_api():
    tracer = DummyTracer()
    api = responder.API()
    api.add_middleware(TraceMiddleware, tracer=tracer)

    @api.route("/login")
    def login(req, resp):
        resp.text = "asdf"

    @api.route("/exception")
    def exception(req, resp):
        raise FakeError("ohno")

    return tracer, api

class FakeError(Exception): pass
