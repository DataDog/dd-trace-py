import json

from ddtrace.internal import _context
from tests.contrib.flask import BaseFlaskTestCase


class FlaskAppSecTestCase(BaseFlaskTestCase):
    def test_flask_simple_attack(self):
        self.tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")
        resp = self.client.get("/.git?q=1")
        assert resp.status_code == 404
        spans = self.pop_spans()
        root_span = spans[0]

        assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
        assert _context.get_item("http.request.uri", span=root_span) == "http://localhost/.git?q=1"
        assert _context.get_item("http.request.query", span=root_span)["q"] == "1"

    def test_flask_querystrings(self):
        self.tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")
        self.client.get("/?a=1&b&c=d")
        spans = self.pop_spans()
        root_span = spans[0]
        assert _context.get_item("http.request.query", span=root_span)["a"] == "1"
        assert _context.get_item("http.request.query", span=root_span)["b"] == ""
        assert _context.get_item("http.request.query", span=root_span)["c"] == "d"
        assert "d" not in _context.get_item("http.request.query", span=root_span)
        self.client.get("/")
        spans = self.pop_spans()
        root_span = spans[0]
        assert len(_context.get_item("http.request.query", span=root_span)) == 0
