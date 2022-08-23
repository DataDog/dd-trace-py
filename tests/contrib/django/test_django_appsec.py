import json

from ddtrace.internal import _context
from ddtrace.internal.compat import urlencode
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.utils import override_env
from tests.utils import override_global_config


def test_django_simple_attack(client, test_spans, tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    assert client.get("/.git?q=1").status_code == 404
    root_span = test_spans.spans[0]
    assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
    assert _context.get_item("http.request.uri", span=root_span) == "http://testserver/.git?q=1"
    assert _context.get_item("http.request.headers", span=root_span) is not None
    query = dict(_context.get_item("http.request.query", span=root_span))
    assert query == {"q": "1"} or query == {"q": ["1"]}


def test_django_querystrings(client, test_spans, tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    client.get("/?a=1&b&c=d")
    root_span = test_spans.spans[0]
    query = dict(_context.get_item("http.request.query", span=root_span))
    assert query == {"a": "1", "b": "", "c": "d"} or query == {"a": ["1"], "b": [""], "c": ["d"]}


def test_no_django_querystrings(client, test_spans, tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    client.get("/")
    root_span = test_spans.spans[0]
    assert not _context.get_item("http.request.query", span=root_span)


def test_django_request_cookies(client, test_spans, tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    client.cookies.load({"mytestingcookie_key": "mytestingcookie_value"})
    client.get("/")
    root_span = test_spans.spans[0]
    query = dict(_context.get_item("http.request.cookies", span=root_span))

    assert root_span.get_tag("_dd.appsec.json") is None
    assert query == {"mytestingcookie_key": "mytestingcookie_value"}


def test_django_request_cookies_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            tracer._appsec_enabled = True
            # Hack: need to pass an argument to configure so that the processors are recreated
            tracer.configure(api_version="v0.4")
            client.cookies.load({"attack": "1' or '1' = '1'"})
            client.get("/")
            root_span = test_spans.spans[0]

            query = dict(_context.get_item("http.request.cookies", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_urlencoded(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})

        response = client.post("/body/", payload, content_type="application/x-www-form-urlencoded")
        assert response.status_code == 200

        root_span = test_spans.spans[0]
        query = dict(_context.get_item("http.request.body", span=root_span))

        assert root_span.get_tag("_dd.appsec.json") is None
        assert query == {"mytestingbody_key": "mytestingbody_value"}


def test_django_request_body_urlencoded_appsec_disabled_then_no_body(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        tracer._appsec_enabled = False
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        client.post("/", payload, content_type="application/x-www-form-urlencoded")
        root_span = test_spans.spans[0]
        assert not _context.get_item("http.request.body", span=root_span)


def test_django_request_body_urlencoded_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = urlencode({"attack": "1' or '1' = '1'"})
        client.post("/body/", payload, content_type="application/x-www-form-urlencoded")
        root_span = test_spans.spans[0]

        query = dict(_context.get_item("http.request.body", span=root_span))
        assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
        assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_json(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = json.dumps({"mytestingbody_key": "mytestingbody_value"})

        response = client.post("/body/", payload, content_type="application/json")
        assert response.status_code == 200
        assert response.content == b'{"mytestingbody_key": "mytestingbody_value"}'

        root_span = test_spans.spans[0]
        query = dict(_context.get_item("http.request.body", span=root_span))

        assert root_span.get_tag("_dd.appsec.json") is None
        assert query == {"mytestingbody_key": "mytestingbody_value"}


def test_django_request_body_json_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            tracer._appsec_enabled = True
            # Hack: need to pass an argument to configure so that the processors are recreated
            tracer.configure(api_version="v0.4")
            payload = json.dumps({"attack": "1' or '1' = '1'"})
            client.post("/", payload, content_type="application/json")
            root_span = test_spans.spans[0]

            query = dict(_context.get_item("http.request.body", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_plain(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = "foo=bar"
        client.post("/", payload, content_type="text/plain")
        root_span = test_spans.spans[0]
        query = _context.get_item("http.request.body", span=root_span)

        assert root_span.get_tag("_dd.appsec.json") is None
        assert query == "foo=bar"


def test_django_request_body_plain_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            tracer._appsec_enabled = True
            # Hack: need to pass an argument to configure so that the processors are recreated
            tracer.configure(api_version="v0.4")
            payload = "1' or '1' = '1'"
            client.post("/", payload, content_type="text/plain")
            root_span = test_spans.spans[0]

            query = _context.get_item("http.request.body", span=root_span)
            assert "triggers" in json.loads(root_span.get_tag("_dd.appsec.json"))
            assert query == "1' or '1' = '1'"


def test_django_path_params(client, test_spans, tracer):
    tracer._appsec_enabled = True
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    client.get("/path-params/2022/july/")
    root_span = test_spans.spans[0]
    path_params = _context.get_item("http.request.path_params", span=root_span)

    assert path_params["month"] == "july"
    # django>=1.8,<1.9 returns string instead int
    assert int(path_params["year"]) == 2022
