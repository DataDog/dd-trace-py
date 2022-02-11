import json

from tests.contrib.flask import BaseFlaskTestCase


class FlaskAppSecTestCase(BaseFlaskTestCase):

    def test_flask_simple_attack(self, test_spans, tracer):
        tracer._initialize_span_processors(appsec_enabled=True)
        assert self.client.get("/.git?q=1").status_code == 404
        root_span = test_spans.spans[0]
        self.assertTrue("triggers" in json.loads(root_span.get_tag("_dd.appsec.json")))
        self.assertEqual(root_span.store.kept_addresses["server.request.uri.raw"], "http://testserver/.git?q=1")
        self.assertEqual(root_span.store.kept_addresses["server.request.query"]["q"], "1")
        self.assertTrue("Cookie" not in root_span.store.kept_addresses["server.request.headers.no_cookies"])
