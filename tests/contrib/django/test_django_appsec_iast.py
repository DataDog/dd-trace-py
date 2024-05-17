# -*- coding: utf-8 -*-
import json

import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import oce
from ddtrace.appsec._iast._patch_modules import patch_iast
from ddtrace.appsec._iast._utils import _is_python_version_supported as python_supported_by_iast
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.internal.compat import urlencode
from ddtrace.settings.asm import config as asm_config
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import override_env
from tests.utils import override_global_config


TEST_FILE = "tests/contrib/django/django_app/appsec_urls.py"


@pytest.fixture(autouse=True)
def reset_context():
    with override_env({"DD_IAST_ENABLED": "True"}):
        from ddtrace.appsec._iast._taint_tracking import create_context
        from ddtrace.appsec._iast._taint_tracking import reset_context

        yield
        reset_context()
        _ = create_context()


def _aux_appsec_get_root_span(
    client,
    test_spans,
    tracer,
    payload=None,
    url="/",
    content_type="text/plain",
    headers=None,
    cookies=None,
):
    if cookies is None:
        cookies = {}
    tracer._asm_enabled = asm_config._asm_enabled
    tracer._iast_enabled = asm_config._iast_enabled
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


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_weak_hash(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True, _iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        patch_iast({"weak_hash": True})
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/appsec/weak-hash/")
        str_json = root_span.get_tag(IAST.JSON)
        assert str_json is not None, "no JSON tag in root span"
        vulnerability = json.loads(str_json)["vulnerabilities"][0]
        assert vulnerability["location"]["path"].endswith(TEST_FILE)
        assert vulnerability["evidence"]["value"] == "md5"


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/taint-checking-enabled/?q=aaa",
            headers={"HTTP_USER_AGENT": "test/1.2.3"},
        )

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_disabled(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False, _deduplication_enabled=False)):
        oce.reconfigure()

        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/taint-checking-disabled/?q=aaa",
            headers={"HTTP_USER_AGENT": "test/1.2.3"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled_sqli_http_request_parameter(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/sqli_http_request_parameter/?q=SELECT 1 FROM sqlite_master",
            headers={"HTTP_USER_AGENT": "test/1.2.3"},
        )

        vuln_type = "SQL_INJECTION"

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        line, hash_value = get_line_and_hash("iast_enabled_sqli_http_request_parameter", vuln_type, filename=TEST_FILE)

        assert loaded["sources"] == [
            {
                "name": "q",
                "origin": "http.request.parameter",
                "pattern": "abcdefghijklmnopqrstuvwxyzA",
                "redacted": True,
            }
        ]

        assert loaded["vulnerabilities"][0]["type"] == vuln_type
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [
                {"source": 0, "value": "SELECT "},
                {"pattern": "h", "redacted": True, "source": 0},
                {"source": 0, "value": " FROM sqlite_master"},
            ]
        }
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled_sqli_http_request_header_value(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/sqli_http_request_header_value/",
            headers={"HTTP_USER_AGENT": "master"},
        )

        assert response.status_code == 200
        assert response.content == b"master"

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        assert loaded["sources"] == [{"origin": "http.request.header", "name": "HTTP_USER_AGENT", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == VULN_SQL_INJECTION
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [
                {"value": "SELECT "},
                {"redacted": True},
                {"value": " FROM sqlite_"},
                {"source": 0, "value": "master"},
            ]
        }

        line, hash_value = get_line_and_hash(
            "iast_enabled_sqli_http_request_header_value", VULN_SQL_INJECTION, filename=TEST_FILE
        )
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_disabled_sqli_http_request_header_value(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/sqli_http_request_header_value/",
            headers={"HTTP_USER_AGENT": "master"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"master"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled_sqli_http_request_header_name(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/sqli_http_request_header_name/",
            headers={"master": "test/1.2.3"},
        )

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        assert loaded["sources"] == [{"origin": "http.request.header.name", "name": "master", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == VULN_SQL_INJECTION
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [
                {"value": "SELECT "},
                {"redacted": True},
                {"value": " FROM sqlite_"},
                {"value": "master", "source": 0},
            ]
        }

        line, hash_value = get_line_and_hash(
            "iast_enabled_sqli_http_request_header_name", VULN_SQL_INJECTION, filename=TEST_FILE
        )
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_disabled_sqli_http_request_header_name(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/sqli_http_request_header_name/",
            headers={"master": "test/1.2.3"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_iast_enabled_full_sqli_http_path_parameter(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_path_parameter/sqlite_master/",
            headers={"HTTP_USER_AGENT": "test/1.2.3"},
        )
        assert response.status_code == 200
        assert response.content == b"test/1.2.3"

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        assert loaded["sources"] == [
            {"origin": "http.request.path.parameter", "name": "q_http_path_parameter", "value": "sqlite_master"}
        ]
        assert loaded["vulnerabilities"][0]["type"] == VULN_SQL_INJECTION
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [
                {"value": "SELECT "},
                {"redacted": True},
                {"value": " from "},
                {"value": "sqlite_master", "source": 0},
            ]
        }
        line, hash_value = get_line_and_hash(
            "iast_enabled_full_sqli_http_path_parameter", VULN_SQL_INJECTION, filename=TEST_FILE
        )
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_iast_disabled_full_sqli_http_path_parameter(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_path_parameter/sqlite_master/",
            headers={"HTTP_USER_AGENT": "test/1.2.3"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled_sqli_http_cookies_name(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_cookie_name/",
            cookies={"master": "test/1.2.3"},
        )
        assert response.status_code == 200
        assert response.content == b"test/1.2.3"

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        vulnerability = False
        for vuln in loaded["vulnerabilities"]:
            if vuln["type"] == VULN_SQL_INJECTION:
                vulnerability = vuln

        assert vulnerability, "No {} reported".format(VULN_SQL_INJECTION)

        assert loaded["sources"] == [{"origin": "http.request.cookie.name", "name": "master", "value": "master"}]
        assert vulnerability["evidence"] == {
            "valueParts": [
                {"value": "SELECT "},
                {"redacted": True},
                {"value": " FROM sqlite_"},
                {"value": "master", "source": 0},
            ]
        }
        line, hash_value = get_line_and_hash(
            "iast_enabled_sqli_http_cookies_name", VULN_SQL_INJECTION, filename=TEST_FILE
        )
        assert vulnerability["location"]["path"] == TEST_FILE
        assert vulnerability["location"]["line"] == line
        assert vulnerability["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_iast_disabled_sqli_http_cookies_name(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_cookie_name/",
            cookies={"master": "test/1.2.3"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled_sqli_http_cookies_value(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_cookie_value/",
            cookies={"master": "master"},
        )
        assert response.status_code == 200
        assert response.content == b"master"

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        vulnerability = False
        for vuln in loaded["vulnerabilities"]:
            if vuln["type"] == VULN_SQL_INJECTION:
                vulnerability = vuln

        assert vulnerability, "No {} reported".format(VULN_SQL_INJECTION)
        assert loaded["sources"] == [{"origin": "http.request.cookie.value", "name": "master", "value": "master"}]
        assert vulnerability["type"] == "SQL_INJECTION"

        assert vulnerability["evidence"] == {
            "valueParts": [
                {"value": "SELECT "},
                {"redacted": True},
                {"value": " FROM sqlite_"},
                {"value": "master", "source": 0},
            ]
        }

        line, hash_value = get_line_and_hash(
            "iast_enabled_sqli_http_cookies_value", VULN_SQL_INJECTION, filename=TEST_FILE
        )
        assert vulnerability["location"]["line"] == line
        assert vulnerability["location"]["path"] == TEST_FILE
        assert vulnerability["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_iast_disabled_sqli_http_cookies_value(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_cookie_value/",
            cookies={"master": "master"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"master"


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        ("master", "application/json"),
        ("master", "text/plain"),
    ],
)
@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled_sqli_http_body(client, test_spans, tracer, payload, content_type):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_body/",
            payload=payload,
            content_type=content_type,
        )
        loaded = json.loads(root_span.get_tag(IAST.JSON))

        line, hash_value = get_line_and_hash("iast_enabled_sqli_http_body", VULN_SQL_INJECTION, filename=TEST_FILE)

        assert loaded["sources"] == [{"origin": "http.request.body", "name": "body", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == VULN_SQL_INJECTION
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [
                {"value": "SELECT "},
                {"redacted": True},
                {"value": " FROM sqlite_"},
                {"value": "master", "source": 0},
            ]
        }
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE

        assert response.status_code == 200
        assert response.content == b"master"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_iast_disabled_sqli_http_body(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_body/",
            payload="master",
            content_type="application/json",
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"master"


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_querydict_django_with_iast(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/validate_querydict/?x=1&y=2&x=3",
        )

        assert root_span.get_tag(IAST.JSON) is None
        assert response.status_code == 200
        assert (
            response.content == b"x=['1', '3'], all=[('x', ['1', '3']), ('y', ['2'])],"
            b" keys=['x', 'y'], urlencode=x=1&x=3&y=2"
        )


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_command_injection(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        patch_iast({"command_injection": True})
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/command-injection/",
            payload="master",
            content_type="application/json",
        )

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        line, hash_value = get_line_and_hash("iast_command_injection", VULN_CMDI, filename=TEST_FILE)

        assert loaded["sources"] == [
            {"name": "body", "origin": "http.request.body", "pattern": "abcdef", "redacted": True}
        ]
        assert loaded["vulnerabilities"][0]["type"] == VULN_CMDI
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "dir "}, {"redacted": True}, {"pattern": "abcdef", "redacted": True, "source": 0}]
        }
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_header_injection(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        patch_iast({"header_injection": True})
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/header-injection/",
            payload="master",
            content_type="application/json",
        )

        loaded = json.loads(root_span.get_tag(IAST.JSON))

        line, hash_value = get_line_and_hash("iast_header_injection", VULN_HEADER_INJECTION, filename=TEST_FILE)

        assert loaded["sources"] == [{"origin": "http.request.body", "name": "body", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == VULN_HEADER_INJECTION
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "Header-Injection: "}, {"source": 0, "value": "master"}]
        }
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_insecure_cookie(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/insecure-cookie/test_insecure/",
        )

        assert root_span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == []
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_INSECURE_COOKIE
        assert vulnerability["evidence"] == {"valueParts": [{"value": "insecure"}]}
        assert "path" not in vulnerability["location"].keys()
        assert "line" not in vulnerability["location"].keys()
        assert vulnerability["location"]["spanId"]
        assert vulnerability["hash"]


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_insecure_cookie_secure(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/insecure-cookie/test_secure/",
        )

        assert root_span.get_metric(IAST.ENABLED) == 1.0

        assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_insecure_cookie_empty_cookie(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/insecure-cookie/test_empty_cookie/",
        )

        assert root_span.get_metric(IAST.ENABLED) == 1.0

        assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_insecure_cookie_2_insecure_1_secure(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/insecure-cookie/test_insecure_2_1/",
        )

        assert root_span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == []
        assert len(loaded["vulnerabilities"]) == 2


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_insecure_cookie_special_characters(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=True, _deduplication_enabled=False)):
        oce.reconfigure()
        root_span, _ = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/insecure-cookie/test_insecure_special/",
        )

        assert root_span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == []
        assert len(loaded["vulnerabilities"]) == 1
        vulnerability = loaded["vulnerabilities"][0]
        assert vulnerability["type"] == VULN_INSECURE_COOKIE
        assert vulnerability["evidence"] == {"valueParts": [{"value": "insecure"}]}
        assert "path" not in vulnerability["location"].keys()
        assert "line" not in vulnerability["location"].keys()
        assert vulnerability["location"]["spanId"]
        assert vulnerability["hash"]
