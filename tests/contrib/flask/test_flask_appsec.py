import json

from tests.contrib.flask import BaseFlaskTestCase


class FlaskAppSecTestCase(BaseFlaskTestCase):
    def test_flask_simple_attack(self):
        self.tracer._initialize_span_processors(appsec_enabled=True)
        resp = self.client.get("/.git?q=1")
        self.assertEqual(resp.status_code, 404)
        spans = self.pop_spans()
        root_span = spans[0]
        self.assertTrue("triggers" in json.loads(root_span.get_tag("_dd.appsec.json")))
        self.assertEqual(root_span.store.kept_addresses["server.request.uri.raw"], "http://localhost/.git?q=1")
        if isinstance(root_span.store.kept_addresses["server.request.query"]["q"], list):
            self.assertEqual(root_span.store.kept_addresses["server.request.query"]["q"], ["1"])
        else:
            self.assertEqual(root_span.store.kept_addresses["server.request.query"]["q"], "1")
        self.assertTrue("Cookie" not in root_span.store.kept_addresses["server.request.headers.no_cookies"])
