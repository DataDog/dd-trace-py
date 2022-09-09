# -*- coding: utf-8 -*-
import json
import logging

import pytest

from ddtrace.constants import APPSEC_JSON
from ddtrace.ext import http
from ddtrace.internal import _context
from ddtrace.internal.compat import urlencode
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.utils import override_env
from tests.utils import override_global_config


def _aux_appsec_get_root_span(
    client, test_spans, tracer, appsec_enabled=True, payload=None, url="/", content_type="text/plain"
):
    tracer._appsec_enabled = appsec_enabled
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    if payload is None:
        response = client.get(url)
    else:
        response = client.post(url, payload, content_type=content_type)
    return test_spans.spans[0], response


def test_django_simple_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/.git?q=1")
        assert response.status_code == 404
        assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
        assert _context.get_item("http.request.uri", span=root_span) == "http://testserver/.git?q=1"
        assert _context.get_item("http.request.headers", span=root_span) is not None
        query = dict(_context.get_item("http.request.query", span=root_span))
        assert query == {"q": "1"} or query == {"q": ["1"]}


def test_django_querystrings(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/?a=1&b&c=d")
        query = dict(_context.get_item("http.request.query", span=root_span))
        assert query == {"a": "1", "b": "", "c": "d"} or query == {"a": ["1"], "b": [""], "c": ["d"]}


def test_no_django_querystrings(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer)
        assert not _context.get_item("http.request.query", span=root_span)


def test_django_request_cookies(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        client.cookies.load({"mytestingcookie_key": "mytestingcookie_value"})
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer)
        query = dict(_context.get_item("http.request.cookies", span=root_span))

        assert root_span.get_tag(APPSEC_JSON) is None
        assert query == {"mytestingcookie_key": "mytestingcookie_value"}


def test_django_request_cookies_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            client.cookies.load({"attack": "1' or '1' = '1'"})
            root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer)
            query = dict(_context.get_item("http.request.cookies", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_urlencoded(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, payload=payload, url="/body/", content_type="application/x-www-form-urlencoded"
        )

        assert response.status_code == 200
        query = dict(_context.get_item("http.request.body", span=root_span))

        assert root_span.get_tag(APPSEC_JSON) is None
        assert query == {"mytestingbody_key": "mytestingbody_value"}


def test_django_request_body_urlencoded_appsec_disabled_then_no_body(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            appsec_enabled=False,
            payload=payload,
            url="/",
            content_type="application/x-www-form-urlencoded",
        )
        assert not _context.get_item("http.request.body", span=root_span)


def test_django_request_body_urlencoded_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = urlencode({"attack": "1' or '1' = '1'"})
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            url="/body/",
            content_type="application/x-www-form-urlencoded",
        )
        query = dict(_context.get_item("http.request.body", span=root_span))
        assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
        assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_json(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = json.dumps({"mytestingbody_key": "mytestingbody_value"})
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            url="/body/",
            content_type="application/json",
        )
        query = dict(_context.get_item("http.request.body", span=root_span))
        assert response.status_code == 200
        assert response.content == b'{"mytestingbody_key": "mytestingbody_value"}'

        assert root_span.get_tag(APPSEC_JSON) is None
        assert query == {"mytestingbody_key": "mytestingbody_value"}


def test_django_request_body_json_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            payload = json.dumps({"attack": "1' or '1' = '1'"})
            root_span, _ = _aux_appsec_get_root_span(
                client,
                test_spans,
                tracer,
                payload=payload,
                content_type="application/json",
            )
            query = dict(_context.get_item("http.request.body", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_xml(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = "<mytestingbody_key>mytestingbody_value</mytestingbody_key>"

        for content_type in ("application/xml", "text/xml"):
            root_span, response = _aux_appsec_get_root_span(
                client,
                test_spans,
                tracer,
                payload=payload,
                url="/body/",
                content_type=content_type,
            )

            query = dict(_context.get_item("http.request.body", span=root_span))
            assert response.status_code == 200
            assert response.content == b"<mytestingbody_key>mytestingbody_value</mytestingbody_key>"
            assert root_span.get_tag(APPSEC_JSON) is None
            assert query == {"mytestingbody_key": "mytestingbody_value"}


def test_django_request_body_xml_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = "<attack>1' or '1' = '1'</attack>"

        for content_type in ("application/xml", "text/xml"):
            root_span, _ = _aux_appsec_get_root_span(
                client,
                test_spans,
                tracer,
                payload=payload,
                content_type=content_type,
            )
            query = dict(_context.get_item("http.request.body", span=root_span))
            assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_plain(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, payload="foo=bar")
        query = _context.get_item("http.request.body", span=root_span)

        assert root_span.get_tag(APPSEC_JSON) is None
        assert query == "foo=bar"


def test_django_request_body_plain_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):

        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, payload="1' or '1' = '1'")

        query = _context.get_item("http.request.body", span=root_span)
        assert "triggers" in json.loads(root_span.get_tag(APPSEC_JSON))
        assert query == "1' or '1' = '1'"


def test_django_request_body_json_empty(caplog, client, test_spans, tracer):
    with caplog.at_level(logging.WARNING), override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_APPSEC_RULES=RULES_GOOD_PATH)
    ):
        payload = '{"attack": "bad_payload",}'

        _, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            content_type="application/json",
        )

        assert response.status_code == 200
        assert "Failed to parse request body" in caplog.text


def test_django_path_params(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/path-params/2022/july/",
        )
        path_params = _context.get_item("http.request.path_params", span=root_span)
        assert path_params["month"] == "july"
        # django>=1.8,<1.9 returns string instead int
        assert int(path_params["year"]) == 2022


def test_django_useragent(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        client.get("/?a=1&b&c=d", HTTP_USER_AGENT="test/1.2.3")
        root_span = test_spans.spans[0]
        assert root_span.get_tag(http.USER_AGENT) == "test/1.2.3"


def test_django_client_ip_disabled(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_TRACE_CLIENT_IP_HEADER_DISABLED="True")
    ):
        client.get("/?a=1&b&c=d", HTTP_X_REAL_IP="8.8.8.8")
        root_span = test_spans.spans[0]
        assert not root_span.get_tag(http.CLIENT_IP)


def test_django_client_ip_header_set_by_env_var_empty(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_TRACE_CLIENT_IP_HEADER="Fooipheader")
    ):
        client.get("/?a=1&b&c=d", HTTP_FOOIPHEADER="", HTTP_X_REAL_IP="8.8.8.8")
        root_span = test_spans.spans[0]
        # X_REAL_IP should be ignored since the client provided a header
        assert not root_span.get_tag(http.CLIENT_IP)


def test_django_client_ip_header_set_by_env_var_invalid(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_TRACE_CLIENT_IP_HEADER="Fooipheader")
    ):
        client.get("/?a=1&b&c=d", HTTP_FOOIPHEADER="foobar", HTTP_X_REAL_IP="8.8.8.8")
        root_span = test_spans.spans[0]
        # X_REAL_IP should be ignored since the client provided a header
        assert not root_span.get_tag(http.CLIENT_IP)


def test_django_client_ip_header_set_by_env_var_valid(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_TRACE_CLIENT_IP_HEADER="X-Use-This")):
        client.get("/?a=1&b&c=d", HTTP_CLIENT_IP="8.8.8.8", HTTP_X_USE_THIS="4.4.4.4")
        root_span = test_spans.spans[0]
        assert root_span.get_tag(http.CLIENT_IP) == "4.4.4.4"


def test_django_client_ip_nothing(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        client.get("/?a=1&b&c=d")
        root_span = test_spans.spans[0]
        assert not root_span.get_tag(http.CLIENT_IP)


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"HTTP_CLIENT_IP": "", "HTTP_X_FORWARDED_FOR": "4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_CLIENT_IP": "192.168.1.3,4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_CLIENT_IP": "4.4.4.4,8.8.8.8"}, "4.4.4.4"),
        ({"HTTP_CLIENT_IP": "192.168.1.10,192.168.1.20"}, "192.168.1.10"),
    ],
)
def test_django_client_ip_headers(client, test_spans, tracer, kwargs, expected):
    with override_global_config(dict(_appsec_enabled=True)):
        client.get("/?a=1&b&c=d", **kwargs)
        root_span = test_spans.spans[0]
        assert root_span.get_tag(http.CLIENT_IP) == expected


def test_django_client_ip_header_set_by_env_var_invalid_2(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_TRACE_CLIENT_IP_HEADER="Fooipheader")
    ):
        result = client.get("/?a=1&b&c=d", HTTP_FOOIPHEADER="", HTTP_X_REAL_IP="アスダス")
        assert result.status_code == 200
        root_span = test_spans.spans[0]
        # X_REAL_IP should be ignored since the client provided a header
        assert not root_span.get_tag(http.CLIENT_IP)
