
import responder

from ddtrace.contrib.responder import TraceMiddleware
from ...utils.tracer import DummyTracer


class TestResponder(object):

    def test_200(self):
        tracer, client = _make_test_client()
        resp = client.get("/login")

        assert resp.ok
        assert resp.status_code == 200

        spans = tracer.writer.pop()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == 'responder.request'
        assert s.get_tag('http.status_code') == '200'
        assert s.get_tag('http.method') == 'GET'


    def test_exceptioon(self):
        tracer, client = _make_test_client()
        resp = client.get("/exception")

        assert not resp.ok
        assert resp.status_code == 500


def _make_test_client():
    tracer = DummyTracer()
    api = responder.API()
    api.add_middleware(TraceMiddleware, tracer=tracer)

    @api.route("/login")
    def login(req, resp):
        resp.text = "asdf"

    @api.route("/exception")
    def exception(req, resp):
        raise FakeError("ohno")

    return tracer, responder.api.TestClient(
        api, base_url="http://;", raise_server_exceptions=False
    )


class FakeError(Exception): pass
