import json


def test_django_simple_attack(client, test_spans, tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    assert client.get("/.git?q=1").status_code == 404
    root_span = test_spans.spans[0]
    assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
    assert root_span._request_store.kept_addresses["server.request.uri.raw"] == "http://testserver/.git?q=1"
    assert root_span._request_store.kept_addresses["server.request.query"]["q"] == "1"
    assert "Cookie" not in root_span._request_store.kept_addresses["server.request.headers.no_cookies"]
