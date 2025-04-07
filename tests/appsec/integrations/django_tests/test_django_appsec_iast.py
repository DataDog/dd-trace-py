# -*- coding: utf-8 -*-
import json
from urllib.parse import urlencode

import pytest

from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.settings.asm import config as asm_config
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import override_global_config


TEST_FILE = "tests/appsec/integrations/django_tests/django_app/views.py"


@pytest.fixture(autouse=True)
def iast_context():
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=False, _iast_request_sampling=100.0)
    ):
        yield


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
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer._recreate()
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


def _aux_appsec_get_root_span_with_exception(
    client,
    test_spans,
    tracer,
    payload=None,
    url="/",
    content_type="text/plain",
    headers=None,
    cookies=None,
):
    try:
        return _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=payload,
            url=url,
            content_type=content_type,
            headers=headers,
            cookies=cookies,
        )
    except Exception:
        return False


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_weak_hash(client, test_spans, tracer):
    root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/appsec/weak-hash/")
    str_json = root_span.get_tag(IAST.JSON)
    assert str_json is not None, "no JSON tag in root span"
    vulnerability = json.loads(str_json)["vulnerabilities"][0]
    assert vulnerability["location"]["path"].endswith(TEST_FILE)
    assert vulnerability["evidence"]["value"] == "md5"


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_weak_hash_span_metrics(client, test_spans, tracer):
    root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/appsec/weak-hash/")
    assert root_span.get_metric(IAST.ENABLED) == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".weak_hash") == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header_name") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_path") > 1.0


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_weak_hash_span_metrics_disabled(client, iast_spans_with_zero_sampling, tracer):
    root_span, _ = _aux_appsec_get_root_span(client, iast_spans_with_zero_sampling, tracer, url="/appsec/weak-hash/")
    assert root_span.get_metric(IAST.ENABLED) == 0.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".weak_hash") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header_name") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_path") is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled(client, test_spans, tracer):
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


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        ("master", "application/json"),
        ("master", "text/plain"),
        ("", "plain"),
        ('{"json": "body"}', "plain"),
    ],
)
@pytest.mark.parametrize(
    ("deduplication"),
    [
        True,
        False,
    ],
)
@pytest.mark.parametrize(
    "sampling",
    [
        0.0,
        100.0,
        50.0,
    ],
)
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_view_with_exception(client, test_spans, tracer, payload, content_type, deduplication, sampling):
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=deduplication, _iast_request_sampling=sampling)
    ):
        response = _aux_appsec_get_root_span_with_exception(
            client,
            test_spans,
            tracer,
            content_type=content_type,
            url="/appsec/view_with_exception/?q=" + payload,
        )

        assert response is False


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_disabled(client, test_spans, tracer):
    with override_global_config(dict(_iast_enabled=False, _iast_deduplication_enabled=False)):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_parameter_metrics(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        payload=urlencode({"SELECT": "unused"}),
        content_type="application/x-www-form-urlencoded",
        url="/appsec/sqli_http_request_parameter_name_post/",
        headers={"HTTP_USER_AGENT": "test/1.2.3"},
    )
    assert root_span.get_metric(IAST.ENABLED) == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".sql_injection") == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") >= 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_body") == 2
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_parameter_name") >= 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header") >= 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header_name") >= 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_path") >= 1.0


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_parameter_metrics_disabled(client, iast_spans_with_zero_sampling, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_spans_with_zero_sampling,
        tracer,
        payload=urlencode({"SELECT": "unused"}),
        content_type="application/x-www-form-urlencoded",
        url="/appsec/sqli_http_request_parameter_name_post/",
        headers={"HTTP_USER_AGENT": "test/1.2.3"},
    )
    assert root_span.get_metric(IAST.ENABLED) == 0.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_REQUEST_TAINTED) is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".sql_injection") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_body") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_parameter_name") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header_name") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_path") is None


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_parameter(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
        content_type="application/x-www-form-urlencoded",
        url="/appsec/sqli_http_request_parameter/?q=SELECT 1 FROM sqlite_master WHERE name='",
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
            "pattern": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMN",
            "redacted": True,
        }
    ]

    assert loaded["vulnerabilities"][0]["type"] == vuln_type
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [
            {"source": 0, "value": "SELECT "},
            {"pattern": "h", "redacted": True, "source": 0},
            {"source": 0, "value": " FROM sqlite_master WHERE name='"},
            {"redacted": True},
            {"value": "'"},
        ]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_parameter_name_get(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        content_type="application/x-www-form-urlencoded",
        url="/appsec/sqli_http_request_parameter_name_get/?SELECT=unused",
        headers={"HTTP_USER_AGENT": "test/1.2.3"},
    )

    vuln_type = "SQL_INJECTION"

    assert response.status_code == 200
    assert response.content == b"test/1.2.3"

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    line, hash_value = get_line_and_hash(
        "iast_enabled_sqli_http_request_parameter_name_get", vuln_type, filename=TEST_FILE
    )

    assert loaded["sources"] == [
        {
            "name": "SELECT",
            "origin": "http.request.parameter.name",
            "value": "SELECT",
        }
    ]

    assert loaded["vulnerabilities"][0]["type"] == vuln_type
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [
            {"source": 0, "value": "SELECT"},
            {
                "value": " ",
            },
            {
                "redacted": True,
            },
        ]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_parameter_name_post(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        payload=urlencode({"SELECT": "unused"}),
        content_type="application/x-www-form-urlencoded",
        url="/appsec/sqli_http_request_parameter_name_post/",
        headers={"HTTP_USER_AGENT": "test/1.2.3"},
    )

    vuln_type = "SQL_INJECTION"

    assert response.status_code == 200
    assert response.content == b"test/1.2.3"

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    line, hash_value = get_line_and_hash(
        "iast_enabled_sqli_http_request_parameter_name_post", vuln_type, filename=TEST_FILE
    )

    assert loaded["sources"] == [
        {
            "name": "SELECT",
            "origin": "http.request.parameter.name",
            "value": "SELECT",
        }
    ]

    assert loaded["vulnerabilities"][0]["type"] == vuln_type
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [
            {"source": 0, "value": "SELECT"},
            {
                "value": " ",
            },
            {
                "redacted": True,
            },
        ]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_query_no_redacted(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/sqli_query_no_redacted/?q=sqlite_master",
    )

    vuln_type = "SQL_INJECTION"

    assert response.status_code == 200
    assert response.content == b"OK"

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    line, hash_value = get_line_and_hash("sqli_query_no_redacted", vuln_type, filename=TEST_FILE)

    assert loaded["sources"] == [
        {
            "name": "q",
            "origin": "http.request.parameter",
            "value": "sqlite_master",
        }
    ]

    assert loaded["vulnerabilities"][0]["type"] == vuln_type
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [
            {"value": "SELECT * FROM "},
            {"source": 0, "value": "sqlite_master"},
            {"value": " ORDER BY name"},
        ]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_header_value(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_request_header_value(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_header_name(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_request_header_name(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_path_parameter(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_path_parameter(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_cookies_name(client, test_spans, tracer):
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
    line, hash_value = get_line_and_hash("iast_enabled_sqli_http_cookies_name", VULN_SQL_INJECTION, filename=TEST_FILE)
    assert vulnerability["location"]["path"] == TEST_FILE
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["method"] == "sqli_http_request_cookie_name"
    assert vulnerability["location"]["class_name"] == ""
    assert vulnerability["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_cookies_name(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_cookies_value(client, test_spans, tracer):
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

    line, hash_value = get_line_and_hash("iast_enabled_sqli_http_cookies_value", VULN_SQL_INJECTION, filename=TEST_FILE)
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["path"] == TEST_FILE
    assert vulnerability["location"]["method"] == "sqli_http_request_cookie_value"
    assert vulnerability["location"]["class_name"] == ""
    assert vulnerability["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_cookies_value(client, test_spans, tracer):
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
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_body(client, test_spans, tracer, payload, content_type):
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

    assert loaded["sources"] == [{"origin": "http.request.body", "name": "http.request.body", "value": "master"}]
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


@pytest.mark.parametrize(
    ("payload", "content_type"),
    [
        ("", "application/json"),
        ("", "text/plain"),
        ("", "application/x-www-form-urlencoded"),
    ],
)
@pytest.mark.parametrize(
    ("deduplication"),
    [
        True,
        False,
    ],
)
@pytest.mark.parametrize(
    "sampling",
    [
        0.0,
        100.0,
        50.0,
    ],
)
@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_tainted_http_body_empty(client, test_spans, tracer, payload, content_type, deduplication, sampling):
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=deduplication, _iast_request_sampling=sampling)
    ):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/source/body/",
            payload=payload,
            content_type=content_type,
        )
        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b""


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_body(client, test_spans, tracer):
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


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_querydict(client, test_spans, tracer):
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


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_command_injection(client, test_spans, tracer):
    patch_common_modules()
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
        {"name": "http.request.body", "origin": "http.request.body", "pattern": "abcdef", "redacted": True}
    ]
    assert loaded["vulnerabilities"][0]["type"] == VULN_CMDI
    assert loaded["vulnerabilities"][0]["hash"] == hash_value
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "dir "}, {"redacted": True}, {"pattern": "abcdef", "redacted": True, "source": 0}]
    }
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_command_injection_span_metrics(client, test_spans, tracer):
    patch_common_modules()
    root_span, _ = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/command-injection/",
        payload="master",
        content_type="application/json",
    )
    assert root_span.get_metric(IAST.ENABLED) == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".command_injection") == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_body") == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header_name") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_path") > 1.0


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_command_injection_span_metrics_disabled(client, iast_spans_with_zero_sampling, tracer):
    patch_common_modules()
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_spans_with_zero_sampling,
        tracer,
        url="/appsec/command-injection/",
        payload="master",
        content_type="application/json",
    )
    assert root_span.get_metric(IAST.ENABLED) == 0.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".command_injection") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_body") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header_name") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header") is None
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_path") is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_header_injection(client, test_spans, tracer):
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

    assert loaded["sources"] == [{"origin": "http.request.body", "name": "http.request.body", "value": "master"}]
    assert loaded["vulnerabilities"][0]["type"] == VULN_HEADER_INJECTION
    assert loaded["vulnerabilities"][0]["hash"] == hash_value
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "Header-Injection: "}, {"source": 0, "value": "master"}]
    }
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie(client, test_spans, tracer):
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
    assert vulnerability["location"]["spanId"]
    assert vulnerability["hash"]
    line, hash_value = get_line_and_hash("test_django_insecure_cookie", VULN_INSECURE_COOKIE, filename=TEST_FILE)
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie_secure(client, test_spans, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/insecure-cookie/test_secure/",
    )

    assert root_span.get_metric(IAST.ENABLED) == 1.0

    assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie_empty_cookie(client, test_spans, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/insecure-cookie/test_empty_cookie/",
    )

    assert root_span.get_metric(IAST.ENABLED) == 1.0

    assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie_2_insecure_1_secure(client, test_spans, tracer):
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


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie_special_characters(client, test_spans, tracer):
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
    assert vulnerability["location"]["spanId"]
    assert vulnerability["hash"]
    line, hash_value = get_line_and_hash(
        "test_django_insecure_cookie_special_characters", VULN_INSECURE_COOKIE, filename=TEST_FILE
    )
    assert vulnerability["location"]["line"] == line
    assert vulnerability["location"]["path"] == TEST_FILE


def test_django_xss(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/xss/?input=<script>alert('XSS')</script>",
    )

    vuln_type = "XSS"

    assert response.status_code == 200
    assert response.content == b"<html>\n<body>\n<p>Input: <script>alert('XSS')</script></p>\n</body>\n</html>"

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    line, hash_value = get_line_and_hash("xss_http_request_parameter_mark_safe", vuln_type, filename=TEST_FILE)

    assert loaded["sources"] == [
        {
            "name": "input",
            "origin": "http.request.parameter",
            "value": "<script>alert('XSS')</script>",
        }
    ]

    assert loaded["vulnerabilities"][0]["type"] == vuln_type
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [
            {"source": 0, "value": "<script>alert('XSS')</script>"},
        ]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


def test_django_xss_safe_template_tag(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/xss/safe/?input=<script>alert('XSS')</script>",
    )

    vuln_type = "XSS"

    assert response.status_code == 200
    assert response.content == b"<html>\n<body>\n<p>Input: <script>alert('XSS')</script></p>\n</body>\n</html>"

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    line, hash_value = get_line_and_hash("xss_http_request_parameter_template_safe", vuln_type, filename=TEST_FILE)

    assert loaded["sources"] == [
        {
            "name": "input",
            "origin": "http.request.parameter",
            "value": "<script>alert('XSS')</script>",
        }
    ]

    assert loaded["vulnerabilities"][0]["type"] == vuln_type
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [
            {"source": 0, "value": "<script>alert('XSS')</script>"},
        ]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


def test_django_xss_autoscape(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/xss/autoscape/?input=<script>alert('XSS')</script>",
    )

    assert response.status_code == 200
    assert (
        response.content
        == b"<html>\n<body>\n<p>\n    &lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;\n</p>\n</body>\n</html>\n"
    ), f"Error. content is {response.content}"

    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


def test_django_xss_secure(client, test_spans, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        test_spans,
        tracer,
        url="/appsec/xss/secure/?input=<script>alert('XSS')</script>",
    )

    assert response.status_code == 200
    assert (
        response.content
        == b"<html>\n<body>\n<p>Input: &lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;</p>\n</body>\n</html>"
    )

    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None
