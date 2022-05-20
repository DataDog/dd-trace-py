import json

import pytest

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
        query = dict(_context.get_item("http.request.query", span=root_span))
        assert query == {"q": "1"} or query == {"q": ["1"]}

    @pytest.mark.skip("broken for now")
    def test_flask_dynamic_url_param(self):
        @self.app.route("/params/<item>")
        def dynamic_url(item):
            return item

        self.tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")
        resp = self.client.get("/params/attack")
        assert resp.status_code == 200
        spans = self.pop_spans()
        root_span = spans[0]
        assert dict(_context.get_item("http.request.path_params", span=root_span)) == {"item": "attack"}

    def test_flask_querystrings(self):
        self.tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        self.tracer.configure(api_version="v0.4")
        self.client.get("/?a=1&b&c=d")
        spans = self.pop_spans()
        root_span = spans[0]
        query = dict(_context.get_item("http.request.query", span=root_span))
        assert query == {"a": "1", "b": "", "c": "d"} or query == {"a": ["1"], "b": [""], "c": ["d"]}
        self.client.get("/")
        spans = self.pop_spans()
        root_span = spans[0]
        assert len(_context.get_item("http.request.query", span=root_span)) == 0
