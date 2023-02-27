# -*- coding: utf-8 -*-
import json
import logging

import pytest

from ddtrace._monkey import patch_iast
from ddtrace.constants import APPSEC_JSON
from ddtrace.constants import IAST_JSON
from ddtrace.ext import http
from ddtrace.internal import _context
from ddtrace.internal import constants
from ddtrace.internal.compat import PY3
from ddtrace.internal.compat import urlencode
from ddtrace.internal.constants import APPSEC_BLOCKED_RESPONSE_JSON
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
    appsec_enabled=True,
    payload=None,
    url="/",
    content_type="text/plain",
    headers=None,
    iast_enabled=False,
):
    tracer._appsec_enabled = appsec_enabled
    tracer._iast_enabled = iast_enabled
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")
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
        str_json = root_span.get_tag(APPSEC_JSON)
        assert str_json is not None, "no JSON tag in root span"
        assert "triggers" in json.loads(str_json)
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
            str_json = root_span.get_tag(APPSEC_JSON)
            assert str_json is not None, "no JSON tag in root span"
            assert "triggers" in json.loads(str_json)
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
        str_json = root_span.get_tag(APPSEC_JSON)
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
            str_json = root_span.get_tag(APPSEC_JSON)
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
            str_json = root_span.get_tag(APPSEC_JSON)
            assert str_json is not None, "no JSON tag in root span"
            assert "triggers" in json.loads(str_json)
            assert query == {"attack": "1' or '1' = '1'"}


def test_django_request_body_plain(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, payload="foo=bar")
        query = _context.get_item("http.request.body", span=root_span)

        assert root_span.get_tag(APPSEC_JSON) is None
        assert query is None


def test_django_request_body_plain_attack(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, payload="1' or '1' = '1'")

        query = _context.get_item("http.request.body", span=root_span)
        str_json = root_span.get_tag(APPSEC_JSON)
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
            url="/path-params/2022/july/",
        )
        path_params = _context.get_item("http.request.path_params", span=root_span)
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
            headers={"HTTP_CLIENT_IP": "8.8.8.8", "HTTP_X_USE_THIS": "4.4.4.4"},
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
        ({"HTTP_CLIENT_IP": "", "HTTP_X_FORWARDED_FOR": "4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_CLIENT_IP": "192.168.1.3,4.4.4.4"}, "4.4.4.4"),
        ({"HTTP_CLIENT_IP": "4.4.4.4,8.8.8.8"}, "4.4.4.4"),
        ({"HTTP_CLIENT_IP": "192.168.1.10,192.168.1.20"}, "192.168.1.10"),
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


def test_django_weak_hash(client, test_spans, tracer):
    with override_env(dict(DD_IAST_ENABLED="true")):
        patch_iast(weak_hash=True)
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/weak-hash/", iast_enabled=True)
        str_json = root_span.get_tag(IAST_JSON)
        assert str_json is not None, "no JSON tag in root span"
        vulnerability = json.loads(str_json)["vulnerabilities"][0]
        assert vulnerability["location"]["path"].endswith("tests/contrib/django/views.py")
        assert vulnerability["evidence"]["value"] == "md5"


def test_request_ipblock_403(client, test_spans, tracer):
    """
    Most blocking tests are done in test_django_snapshots but
    since those go through ASGI, this tests the blocking
    using the "normal" path for these Django tests.
    (They're also a lot less cumbersome to use for experimentation/debugging)
    """
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_X_REAL_IP": _BLOCKED_IP}
        )
        assert result.status_code == 403
        as_bytes = (
            bytes(constants.APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else constants.APPSEC_BLOCKED_RESPONSE_JSON
        )
        assert result.content == as_bytes
        assert root.get_tag("actor.ip") == _BLOCKED_IP


def test_request_ipblock_nomatch_200(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        _, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_X_REAL_IP": _ALLOWED_IP}
        )
        assert result.status_code == 200
        assert result.content == b"Hello, test app."


def test_request_block_request_callable(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        _, result = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/block/", headers={"HTTP_X_REAL_IP": _ALLOWED_IP}
        )
        # Should not block by IP, but the block callable is called directly inside that view
        assert result.status_code == 403
        as_bytes = (
            bytes(constants.APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else constants.APPSEC_BLOCKED_RESPONSE_JSON
        )
        assert result.content == as_bytes


_BLOCKED_USER = "123456"
_ALLOWED_USER = "111111"


def test_request_userblock_200(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(client, test_spans, tracer, url="/checkuser/%s/" % _ALLOWED_USER)
        assert result.status_code == 200


def test_request_userblock_403(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        root, result = _aux_appsec_get_root_span(client, test_spans, tracer, url="/checkuser/%s/" % _BLOCKED_USER)
        assert result.status_code == 403
        as_bytes = (
            bytes(constants.APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else constants.APPSEC_BLOCKED_RESPONSE_JSON
        )
        assert result.content == as_bytes


def test_request_suspicious_request_block_match_method(client, test_spans, tracer):
    # GET must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_METHOD)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/")
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-006"]
    # POST must pass
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_METHOD)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/", payload="any")
        assert response.status_code == 200


def test_request_suspicious_request_block_match_uri(client, test_spans, tracer):
    # .git must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/.git")
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-002"]
    # legit must pass
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/legit")
        assert response.status_code == 404


def test_request_suspicious_request_block_match_path_params(client, test_spans, tracer):
    # value AiKfOeRcvG45 must be blocked
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/path-params/2022/AiKfOeRcvG45/"
        )
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-007"]


def test_request_suspicious_request_block_match_query_value(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="index.html?toto=xtrace")
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-001"]


def test_request_suspicious_request_block_match_header(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(
            client, test_spans, tracer, url="/", headers={"HTTP_USER_AGENT": "01972498723465"}
        )
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-004"]


def test_request_suspicious_request_block_match_body(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/",
            payload='{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}',
            content_type="application/json",
        )
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-003"]


def test_request_suspicious_request_block_match_response_code(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB_RESPONSE)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/do_not_exist.php")
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-005"]


def test_request_suspicious_request_block_match_request_cookie(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        client.cookies.load({"mytestingcookie_key": "jdfoSDGFkivRG_234"})
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="")
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-008"]


def test_request_suspicious_request_block_match_response_headers(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_SRB)):
        tracer._appsec_enabled = True
        # # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        root_span, response = _aux_appsec_get_root_span(client, test_spans, tracer, url="/response-header/")
        assert response.status_code == 403
        as_bytes = bytes(APPSEC_BLOCKED_RESPONSE_JSON, "utf-8") if PY3 else APPSEC_BLOCKED_RESPONSE_JSON
        assert response.content == as_bytes
        loaded = json.loads(root_span.get_tag(APPSEC_JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-009"]
