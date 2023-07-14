# -*- coding: utf-8 -*-
import json
import logging

import pytest

from ddtrace import config
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.ext import http
from ddtrace.ext import user
from ddtrace.internal import constants
from ddtrace.internal import core
from ddtrace.internal.compat import PY3
from ddtrace.internal.compat import urlencode
from ddtrace.internal.constants import BLOCKED_RESPONSE_HTML
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.appsec.test_processor import RULES_SRB
from tests.appsec.test_processor import RULES_SRB_METHOD
from tests.appsec.test_processor import RULES_SRB_RESPONSE
from tests.appsec.test_processor import _ALLOWED_IP
from tests.appsec.test_processor import _BLOCKED_IP
from tests.utils import override_env
from tests.utils import override_global_config


def _aux_appsec_get_root_span(
    client,
    test_spans,
    tracer,
    payload=None,
    url="/",
    content_type="text/plain",
    headers=None,
    cookies={},
):
    tracer._appsec_enabled = config._appsec_enabled
    tracer._iast_enabled = config._iast_enabled
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
    # Set cookies
    client.cookies.load(cookies)
    if payload is None:
        if headers:
            response = client.get(url, **headers)
        else:
            response = client.get(url)
    else:
        if headers:
            response = client.post(url, payload, content_type=content_type, **headers)
        else:
            response = client.post(url, payload, content_type=content_type)
    return test_spans.spans[0], response


def test_django_simple_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/.git?q=1")
        assert response.status_code == 404
        str_json = root_span.get_tag(APPSEC.JSON)
        assert str_json is not None, "no JSON tag in root span"
        assert "triggers" in json.loads(str_json)
        assert core.get_item("http.request.uri", span=root_span) == "http://testserver/.git?q=1"
        assert core.get_item("http.request.headers", span=root_span) is not None
        query = dict(core.get_item("http.request.query", span=root_span))
        assert query == {"q": "1"} or query == {"q": ["1"]}


def test_django_querystrings(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/?a=1&b&c=d")
        query = dict(core.get_item("http.request.query", span=root_span))
        assert query == {"a": "1", "b": "", "c": "d"} or query == {"a": ["1"], "b": [""], "c": ["d"]}


def test_no_django_querystrings(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer)
        assert not core.get_item("http.request.query", span=root_span)


def test_django_request_cookies(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(
            client, test_spans, tracer, cookies={"mytestingcookie_key": "mytestingcookie_value"}
        )
        query = dict(core.get_item("http.request.cookies", span=root_span))

        assert root_span.get_tag(APPSEC.JSON) is None
        assert query == {"mytestingcookie_key": "mytestingcookie_value"}


def test_django_request_cookies_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        with override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, cookies={"attack": "1' or '1' = '1'"})
            query = dict(core.get_item("http.request.cookies", span=root_span))
            str_json = root_span.get_tag(APPSEC.JSON)
            assert str_json is not None, "no JSON tag in root span"
            assert "triggers" in json.loads(str_json)
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_urlencoded(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            url="/appsec/body/",
            content_type="application/x-www-form-urlencoded",
        )

        assert response.status_code == 200
        query = dict(core.get_item("http.request.body", span=root_span))

        assert root_span.get_tag(APPSEC.JSON) is None
        assert query == {"mytestingbody_key": "mytestingbody_value"}


def test_django_request_body_urlencoded_appsec_disabled_then_no_body(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            url="/",
            content_type="application/x-www-form-urlencoded",
        )
        assert not core.get_item("http.request.body", span=root_span)


def test_django_request_body_urlencoded_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = urlencode({"attack": "1' or '1' = '1'"})
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            url="/appsec/body/",
            content_type="application/x-www-form-urlencoded",
        )
        query = dict(core.get_item("http.request.body", span=root_span))
        str_json = root_span.get_tag(APPSEC.JSON)
        assert str_json is not None, "no JSON tag in root span"
        assert "triggers" in json.loads(str_json)
        assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_json(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        payload = json.dumps({"mytestingbody_key": "mytestingbody_value"})
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            url="/appsec/body/",
            content_type="application/json",
        )
        query = dict(core.get_item("http.request.body", span=root_span))
        assert response.status_code == 200
        assert response.content == b'{"mytestingbody_key": "mytestingbody_value"}'

        assert root_span.get_tag(APPSEC.JSON) is None
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
            query = dict(core.get_item("http.request.body", span=root_span))
            str_json = root_span.get_tag(APPSEC.JSON)
            assert str_json is not None, "no JSON tag in root span"
            assert "triggers" in json.loads(str_json)
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
                url="/appsec/body/",
                content_type=content_type,
            )

            query = dict(core.get_item("http.request.body", span=root_span))
            assert response.status_code == 200
            assert response.content == b"<mytestingbody_key>mytestingbody_value</mytestingbody_key>"
            assert root_span.get_tag(APPSEC.JSON) is None
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
            query = dict(core.get_item("http.request.body", span=root_span))
            str_json = root_span.get_tag(APPSEC.JSON)
            assert str_json is not None, "no JSON tag in root span"
            assert "triggers" in json.loads(str_json)
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_plain(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, payload="foo=bar")
        query = core.get_item("http.request.body", span=root_span)

        assert root_span.get_tag(APPSEC.JSON) is None
        assert query is None


def test_django_request_body_plain_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, payload="1' or '1' = '1'")

        query = core.get_item("http.request.body", span=root_span)
        str_json = root_span.get_tag(APPSEC.JSON)
        assert str_json is None, "JSON tag in root span"
        assert query is None


def test_django_request_body_json_bad(caplog, client, test_spans, tracer):
    # Note: there is some odd interaction between hypotheses or pytest and
    # caplog where if you set this to WARNING the second test won't get
    # output unless you set all to DEBUG.
    with caplog.at_level(logging.DEBUG), override_global_config(dict(_appsec_enabled=True)), override_env(
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


def test_django_request_body_xml_bad_logs_warning(caplog, client, test_spans, tracer):
    # see above about caplog
    with caplog.at_level(logging.DEBUG), override_global_config(dict(_appsec_enabled=True)), override_env(
        dict(DD_APPSEC_RULES=RULES_GOOD_PATH)
    ):
        _, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload="bad xml",
            content_type="application/xml",
        )

        assert response.status_code == 200
        assert "Failed to parse request body" in caplog.text


def test_django_path_params(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/july/",
        )
        path_params = core.get_item("http.request.path_params", span=root_span)
        assert path_params["month"] == "july"
        # django>=1.8,<1.9 returns string instead int
        assert int(path_params["year"]) == 2022


def test_django_useragent(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        tracer.configure(api_version="v0.4")
        root_span, _ = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/?a=1&b&c=d", headers={"HTTP_USER_AGENT": "test/1.2.3"}
        )
        assert root_span.get_tag(http.USER_AGENT) == "test/1.2.3"


def test_django_client_ip_asm_enabled_reported(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/?a=1&b&c=d", headers={"HTTP_X_REAL_IP": "8.8.8.8"}
        )
        assert root_span.get_tag(http.CLIENT_IP)


def test_django_client_ip_asm_disabled_not_reported(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=False)):
        root_span, _ = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/?a=1&b&c=d", headers={"HTTP_X_REAL_IP": "8.8.8.8"}
        )
        assert not root_span.get_tag(http.CLIENT_IP)


def test_django_client_ip_header_set_by_env_var_empty(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True, client_ip_header="Fooipheader")):
        root_span, _ = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/?a=1&b&c=d", headers={"HTTP_FOOIPHEADER": "", "HTTP_X_REAL_IP": "8.8.8.8"}
        )
        # X_REAL_IP should be ignored since the client provided a header
        assert not root_span.get_tag(http.CLIENT_IP)


def test_django_client_ip_header_set_by_env_var_invalid(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True, client_ip_header="Fooipheader")):
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/?a=1&b&c=d",
            headers={"HTTP_FOOIPHEADER": "foobar", "HTTP_X_REAL_IP": "8.8.8.8"},
        )
        # X_REAL_IP should be ignored since the client provided a header
        assert not root_span.get_tag(http.CLIENT_IP)


def test_django_client_ip_header_set_by_env_var_valid(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True, client_ip_header="X-Use-This")):
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/?a=1&b&c=d",
            headers={"HTTP_X_CLIENT_IP": "8.8.8.8", "HTTP_X_USE_THIS": "4.4.4.4"},
        )
        assert root_span.get_tag(http.CLIENT_IP) == "4.4.4.4"


def test_django_client_ip_nothing(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/?a=1&b&c=d")
        ip = root_span.get_tag(http.CLIENT_IP)
        assert not ip or ip == "127.0.0.1"  # this varies when running under PyCharm or CI


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"HTTP_X_CLIENT_IP": "", "HTTP_X_FORWARDED_FOR": "4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_X_CLIENT_IP": "192.168.1.3,4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_X_CLIENT_IP": "4.4.4.4,8.8.8.8"}, "4.4.4.4"),
        ({"HTTP_X_CLIENT_IP": "192.168.1.10,192.168.1.20"}, "192.168.1.10"),
    ],
)
def test_django_client_ip_headers(client, test_spans, tracer, kwargs, expected):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/?a=1&b&c=d", headers=kwargs)
        assert root_span.get_tag(http.CLIENT_IP) == expected


def test_django_client_ip_header_set_by_env_var_invalid_2(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True, client_ip_header="Fooipheader")):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/?a=1&b&c=d", headers={"HTTP_FOOIPHEADER": "", "HTTP_X_REAL_IP": "アスダス"}
        )
        assert response.status_code == 200
        # X_REAL_IP should be ignored since the client provided a header
        assert not root_span.get_tag(http.CLIENT_IP)


def test_request_ipblock_403(client, test_spans, tracer):
    """
    Most blocking tests are done in test_django_snapshots but
    since those go through ASGI, this tests the blocking
    using the "normal" path for these Django tests.
    (They're also a lot less cumbersome to use for experimentation/debugging)
    """
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/foobar",
            headers={"HTTP_X_REAL_IP": _BLOCKED_IP, "HTTP_USER_AGENT": "fooagent"},
        )
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else constants.BLOCKED_RESPONSE_JSON
        assert result.content == as_bytes
        assert root.get_tag("actor.ip") == _BLOCKED_IP
        assert root.get_tag(http.STATUS_CODE) == "403"
        assert root.get_tag(http.URL) == "http://testserver/foobar"
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(http.USER_AGENT) == "fooagent"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "text/json"


def test_request_ipblock_403_html(client, test_spans, tracer):
    """
    Most blocking tests are done in test_django_snapshots but
    since those go through ASGI, this tests the blocking
    using the "normal" path for these Django tests.
    (They're also a lot less cumbersome to use for experimentation/debugging)
    """
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_X_REAL_IP": _BLOCKED_IP, "HTTP_ACCEPT": "text/html"}
        )
        assert result.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_HTML, "utf-8") if PY3 else BLOCKED_RESPONSE_HTML
        assert result.content == as_bytes
        assert root.get_tag("actor.ip") == _BLOCKED_IP
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/html"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "text/html"


def test_request_ipblock_nomatch_200(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_X_REAL_IP": _ALLOWED_IP}
        )
        assert result.status_code == 200
        assert result.content == b"Hello, test app."
        assert root.get_tag(http.STATUS_CODE) == "200"


def test_request_block_request_callable(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/block/",
            headers={"HTTP_X_REAL_IP": _ALLOWED_IP, "HTTP_USER_AGENT": "fooagent"},
        )
        # Should not block by IP, but the block callable is called directly inside that view
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else constants.BLOCKED_RESPONSE_JSON
        assert result.content == as_bytes
        assert root.get_tag(http.STATUS_CODE) == "403"
        assert root.get_tag(http.URL) == "http://testserver/appsec/block/"
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(http.USER_AGENT) == "fooagent"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "text/json"


_BLOCKED_USER = "123456"
_ALLOWED_USER = "111111"


def test_request_userblock_200(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/checkuser/%s/" % _ALLOWED_USER
        )
        assert result.status_code == 200
        assert root.get_tag(http.STATUS_CODE) == "200"


def test_request_userblock_403(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/checkuser/%s/" % _BLOCKED_USER
        )
        assert result.status_code == 403
        as_bytes = bytes(constants.BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else constants.BLOCKED_RESPONSE_JSON
        assert result.content == as_bytes
        assert root.get_tag(http.STATUS_CODE) == "403"
        assert root.get_tag(http.URL) == "http://testserver/appsec/checkuser/%s/" % _BLOCKED_USER
        assert root.get_tag(http.METHOD) == "GET"
        assert root.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
        if hasattr(result, "headers"):
            assert result.headers["content-type"] == "text/json"


def test_request_suspicious_request_block_match_method(client, test_spans, tracer):
    # GET must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_METHOD)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/")
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-006"]
        assert root_span.get_tag(http.STATUS_CODE) == "403"
        assert root_span.get_tag(http.URL) == "http://testserver/"
        assert root_span.get_tag(http.METHOD) == "GET"
        assert root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
        if hasattr(response, "headers"):
            assert response.headers["content-type"] == "text/json"
    # POST must pass
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_METHOD)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/", payload="any")
        assert response.status_code == 200
    # GET must pass if appsec disabled
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_METHOD)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/")
        assert response.status_code == 200


def test_request_suspicious_request_block_match_uri(client, test_spans, tracer):
    # .git must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/.git")
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-002"]
    # legit must pass
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/legit")
        assert response.status_code == 404
    # appsec disabled must not block
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/.git")
        assert response.status_code == 404
    # we must block with uri.raw not containing scheme or netloc
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/we_should_block")
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-010"]


def test_request_suspicious_request_block_match_path_params(client, test_spans, tracer):
    # value AiKfOeRcvG45 must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/path-params/2022/AiKfOeRcvG45/"
        )
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-007"]
    # other values must not be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/appsec/path-params/2022/Anything/")
        assert response.status_code == 200
    # appsec disabled must not block
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/appsec/path-params/2022/AiKfOeRcvG45/"
        )
        assert response.status_code == 200


def test_request_suspicious_request_block_match_query_value(client, test_spans, tracer):
    # value xtrace must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="index.html?toto=xtrace")
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-001"]
    # other values must not be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="index.html?toto=ytrace")
        assert response.status_code == 404
    # appsec disabled must not block
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="index.html?toto=xtrace")
        assert response.status_code == 404


def test_request_suspicious_request_block_match_header(client, test_spans, tracer):
    # value 01972498723465 must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_USER_AGENT": "01972498723465"}
        )
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-004"]
    # other values must not be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_USER_AGENT": "01973498523465"}
        )
        assert response.status_code == 200
    # appsec disabled must not block
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_USER_AGENT": "01972498723465"}
        )
        assert response.status_code == 200


def test_request_suspicious_request_block_match_body(client, test_spans, tracer):
    # value asldhkuqwgervf must be blocked
    for appsec in (True, False):
        for payload, content_type, blocked in [
            # json body must be blocked
            ('{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "application/json", True),
            ('{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "text/json", True),
            # xml body must be blocked
            (
                '<?xml version="1.0" encoding="UTF-8"?><attack>yqrweytqwreasldhkuqwgervflnmlnli</attack>',
                "text/xml",
                True,
            ),
            # form body must be blocked
            ("attack=yqrweytqwreasldhkuqwgervflnmlnli", "application/x-www-form-urlencoded", True),
            (
                '--52d1fb4eb9c021e53ac2846190e4ac72\r\nContent-Disposition: form-data; name="attack"\r\n'
                'Content-Type: application/json\r\n\r\n{"test": "yqrweytqwreasldhkuqwgervflnmlnli"}\r\n'
                "--52d1fb4eb9c021e53ac2846190e4ac72--\r\n",
                "multipart/form-data; boundary=52d1fb4eb9c021e53ac2846190e4ac72",
                True,
            ),
            # raw body must not be blocked
            ("yqrweytqwreasldhkuqwgervflnmlnli", "text/plain", False),
            # other values must not be blocked
            ('{"attack": "zqrweytqwreasldhkuqxgervflnmlnli"}', "application/json", False),
        ]:
            with override_global_config(dict(_appsec_enabled=appsec)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
                root_span, response = _aux_appsec_get_root_span(
                    client,
                    test_spans,
                    tracer,
                    url="/",
                    payload=payload,
                    content_type=content_type,
                )
                if appsec and blocked:
                    assert response.status_code == 403, (payload, content_type, blocked, appsec)
                    as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
                    assert response.content == as_bytes
                    loaded = json.loads(root_span.get_tag(APPSEC.JSON))
                    assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-003"]
                else:
                    assert response.status_code == 200


def test_request_suspicious_request_block_match_response_code(client, test_spans, tracer):
    # 404 must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_RESPONSE)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/do_not_exist.php")
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-005"]
    # 200 must not be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_RESPONSE)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/")
        assert response.status_code == 200
    # appsec disabled must not block
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_RESPONSE)):
        _, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/do_not_exist.php")
        assert response.status_code == 404


def test_request_suspicious_request_block_match_request_cookie(client, test_spans, tracer):
    # value jdfoSDGFkivRG_234 must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="", cookies={"mytestingcookie_key": "jdfoSDGFkivRG_234"}
        )
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-008"]
    # other value must not be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="", cookies={"mytestingcookie_key": "jdfoSDGEkivRH_234"}
        )
        assert response.status_code == 200
    # appsec disabled must not block
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        _, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="", cookies={"mytestingcookie_key": "jdfoSDGFkivRG_234"}
        )
        assert response.status_code == 200


def test_request_suspicious_request_block_match_response_headers(client, test_spans, tracer):
    # value MagicKey_Al4h7iCFep9s1 must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/appsec/response-header/")
        assert response.status_code == 403
        as_bytes = bytes(BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-009"]
    # appsec disabled must not block
    with override_global_config(dict(_appsec_enabled=False)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/appsec/response-header/")
        assert response.status_code == 200


@pytest.mark.django_db
def test_django_login_events_disabled_explicitly(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_appsec_enabled=True, _automatic_login_events_mode="disabled")):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated

        with pytest.raises(AssertionError) as excl_info:
            _ = test_spans.find_span(name="django.contrib.auth.login")
        assert "No span found for filter" in str(excl_info.value)


@pytest.mark.django_db
def test_django_login_events_disabled_noappsec(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_appsec_enabled=False, _automatic_login_events_mode="safe")):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated

        with pytest.raises(AssertionError) as excl_info:
            _ = test_spans.find_span(name="django.contrib.auth.login")
        assert "No span found for filter" in str(excl_info.value)


@pytest.mark.django_db
def test_django_login_sucess_extended(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_appsec_enabled=True, _automatic_login_events_mode="extended")):
        test_user = User.objects.create(username="fred", first_name="Fred", email="fred@test.com")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(user.ID) == "fred"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.track") == "true"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.auto.mode") == "extended"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login") == "fred"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.email") == "fred@test.com"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.username") == "Fred"


@pytest.mark.django_db
def test_django_login_sucess_safe(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_appsec_enabled=True, _automatic_login_events_mode="safe")):
        test_user = User.objects.create(username="fred2")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred2", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(user.ID) == "1"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.track") == "true"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.auto.mode") == "safe"
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.login")
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.email")
        assert not login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.username")


@pytest.mark.django_db
def test_django_login_sucess_safe_is_default_if_wrong(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_appsec_enabled=True, _automatic_login_events_mode="foobar")):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag(user.ID) == "1"


@pytest.mark.django_db
def test_django_login_sucess_safe_is_default_if_missing(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(dict(_appsec_enabled=True)):
        test_user = User.objects.create(username="fred")
        test_user.set_password("secret")
        test_user.save()
        client.login(username="fred", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag(user.ID) == "1"


@pytest.mark.django_db
def test_django_login_failure_user_doesnt_exists(client, test_spans, tracer):
    from django.contrib.auth import get_user

    with override_global_config(dict(_appsec_enabled=True, _automatic_login_events_mode="extended")):
        assert not get_user(client).is_authenticated
        client.login(username="missing", password="secret2")
        assert not get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".failure.track") == "true"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".failure." + user.ID) == "missing"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".failure." + user.EXISTS) == "false"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".failure.auto.mode") == "extended"


@pytest.mark.django_db
def test_django_login_sucess_safe_but_user_set_login(client, test_spans, tracer):
    from django.contrib.auth import get_user
    from django.contrib.auth.models import User

    with override_global_config(
        dict(_appsec_enabled=True, _user_model_login_field="username", _automatic_login_events_mode="safe")
    ):
        test_user = User.objects.create(username="fred2")
        test_user.set_password("secret")
        test_user.save()
        assert not get_user(client).is_authenticated
        client.login(username="fred2", password="secret")
        assert get_user(client).is_authenticated
        login_span = test_spans.find_span(name="django.contrib.auth.login")
        assert login_span
        assert login_span.get_tag(user.ID) == "fred2"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.track") == "true"
        assert login_span.get_tag(APPSEC.USER_LOGIN_EVENT_PREFIX + ".success.auto.mode") == "safe"
