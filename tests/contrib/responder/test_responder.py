
import responder

from ddtrace.contrib.responder.patch import patch, unpatch
from ddtrace.pin import Pin

from ...utils.tracer import DummyTracer


class TestResponder(object):

    def setup_method(self):
        patch()

    def teardown_method(self):
        unpatch()

    def test_404(self):
        tracer, api = _make_test_api()
        resp = api.session().get("/no-way-jose-1234")

        assert resp.status_code == 404

        spans = tracer.writer.pop()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == 'responder.request'
        assert s.resource == '404'
        assert s.get_tag('http.status_code') == '404'
        assert s.get_tag('http.method') == 'GET'

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

    def test_routing(self):
        tracer, api = _make_test_api()
        resp = api.session().get("/home/wayne")

        assert resp.ok
        assert resp.status_code == 200
        assert resp.text == 'hello wayne'

        spans = tracer.writer.pop()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == 'responder.request'
        assert s.resource == '/home/{user}'
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
            "x-datadog-trace-id": "123",
            "x-datadog-parent-id": "456",
        })

        assert resp.ok
        assert resp.status_code == 200

        spans = tracer.writer.pop()
        assert len(spans) == 1
        s = spans[0]
        assert s.name == 'responder.request'
        assert s.resource == '/login'
        assert s.trace_id == 123
        assert s.parent_id == 456

    def test_render_template(self):
        tracer, api = _make_test_api()
        resp = api.session().get("/template")

        assert resp.ok
        assert resp.status_code == 200
        assert resp.text == "render that"

        spans = tracer.writer.pop()
        assert len(spans) == 2
        spans.sort(key=lambda s: s.name)
        s1 = spans[1]
        assert s1.name == 'responder.request'
        assert s1.resource == '/template'

        s2 = spans[0]
        assert s2.name == 'responder.render_template'

    def test_custom_child(self):
        tracer, api = _make_test_api()
        resp = api.session().get("/child")

        assert resp.ok
        assert resp.status_code == 200
        assert resp.text == "child"

        spans = tracer.writer.pop()
        assert len(spans) == 2
        spans.sort(key=lambda s: s.name)
        s1 = spans[0]
        s2 = spans[1]
        assert s1.name == 'custom'
        assert s2.name == 'responder.request'
        assert s1.trace_id == s2.trace_id
        assert s1.parent_id == s2.span_id


def _make_test_api():
    tracer = DummyTracer()
    api = responder.API()

    Pin.override(api, tracer=tracer)

    @api.route("/login")
    def login(req, resp):
        resp.text = "asdf"

    @api.route("/template")
    def template(req, resp):
        resp.text = api.template_string("render {{ this}}", this="that")

    @api.route("/home/{user}")
    def home(req, resp, *, user):
        resp.text = f"hello {user}"

    @api.route("/child")
    def child(req, resp):
        with tracer.trace("custom"):
            resp.text = "child"

    @api.route("/exception")
    def exception(req, resp):
        raise FakeError("ohno")

    return tracer, api


class FakeError(Exception):
    pass
