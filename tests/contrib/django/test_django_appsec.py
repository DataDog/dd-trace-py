import json


def test_django_simple_attack(client, test_spans, tracer):
    tracer._initialize_span_processors(appsec_enabled=True)
    assert client.get("/.git?q=1").status_code == 404
    root_span = test_spans.spans[0]
    assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
    assert root_span.store.kept_addresses["server.request.uri.raw"] == "http://testserver/.git?q=1"
    assert root_span.store.kept_addresses["server.request.query"]["q"] == "1"
    assert "Cookie" not in root_span.store.kept_addresses["server.request.headers.no_cookies"]
