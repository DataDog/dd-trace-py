import json
from urllib.parse import urlencode

from django import VERSION as DJANGO_VERSION
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec._constants import IAST_SPAN_TAGS
from ddtrace.appsec._constants import STACK_TRACE
from ddtrace.appsec._iast._patch_modules import _apply_custom_security_controls
from ddtrace.appsec._iast._patch_modules import _unapply_security_control
from ddtrace.appsec._iast.constants import VULN_CMDI
from ddtrace.appsec._iast.constants import VULN_HEADER_INJECTION
from ddtrace.appsec._iast.constants import VULN_INSECURE_COOKIE
from ddtrace.appsec._iast.constants import VULN_SQL_INJECTION
from ddtrace.appsec._iast.constants import VULN_SSRF
from ddtrace.appsec._iast.constants import VULN_STACKTRACE_LEAK
from ddtrace.appsec._iast.constants import VULN_UNVALIDATED_REDIRECT
from ddtrace.settings.asm import config as asm_config
from tests.appsec.iast.conftest import _end_iast_context_and_oce
from tests.appsec.iast.conftest import _start_iast_context_and_oce
from tests.appsec.iast.iast_utils import get_line_and_hash
from tests.utils import TracerSpanContainer
from tests.utils import override_global_config


TEST_FILE = "tests/appsec/integrations/django_tests/django_app/views.py"


def get_iast_stack_trace(root_span):
    appsec_traces = root_span.get_struct_tag(STACK_TRACE.TAG) or {}
    stacks = appsec_traces.get("vulnerability", [])
    return stacks


def _aux_appsec_get_root_span(
    client,
    iast_span,
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
    return iast_span.spans[0], response


def _aux_appsec_get_root_span_with_exception(
    client,
    iast_span,
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
            iast_span,
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
def test_django_weak_hash(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(client, iast_span, tracer, url="/appsec/weak-hash/")
    str_json = root_span.get_tag(IAST.JSON)
    assert str_json is not None, "no JSON tag in root span"
    vulnerability = json.loads(str_json)["vulnerabilities"][0]
    assert vulnerability["location"]["path"].endswith(TEST_FILE)
    assert type(vulnerability["location"]["spanId"]) is int
    assert vulnerability["location"]["spanId"] > 0
    assert vulnerability["location"]["stackId"] == "1"
    assert vulnerability["location"]["line"] > 0
    assert vulnerability["location"]["method"] == "weak_hash_view"
    assert "class" not in vulnerability["location"]
    assert vulnerability["evidence"]["value"] == "md5"


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_weak_hash_span_metrics(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(client, iast_span, tracer, url="/appsec/weak-hash/")
    assert root_span.get_metric(IAST.ENABLED) == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".weak_hash") == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") >= 1.0
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
def test_django_tainted_user_agent_iast_enabled(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_view_with_exception(client, iast_span, tracer, payload, content_type, deduplication, sampling):
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=deduplication, _iast_request_sampling=sampling)
    ):
        response = _aux_appsec_get_root_span_with_exception(
            client,
            iast_span,
            tracer,
            content_type=content_type,
            url="/appsec/view_with_exception/?q=" + payload,
        )

        assert response is False


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_disabled(client, iast_span, tracer):
    with override_global_config(dict(_iast_enabled=False, _iast_deduplication_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
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
def test_django_sqli_http_request_parameter_metrics(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_sqli_http_request_parameter(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
    assert loaded["vulnerabilities"][0]["location"]["spanId"] > 0
    assert loaded["vulnerabilities"][0]["location"]["stackId"] == "1"
    assert loaded["vulnerabilities"][0]["location"]["method"] == "sqli_http_request_parameter"
    assert "class" not in loaded["vulnerabilities"][0]["location"]
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_parameter_name_get_and_stacktrace(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        content_type="application/x-www-form-urlencoded",
        url="/appsec/sqli_http_request_parameter_name_get/?SELECT=unused",
        headers={"HTTP_USER_AGENT": "test/1.2.3"},
    )

    vuln_type = "SQL_INJECTION"

    assert response.status_code == 200
    assert response.content == b"test/1.2.3"

    # api_version = iast_span.tracer._span_aggregator.writer._api_version
    # assert api_version == "v0.4", f"Agent API version {api_version} not supported"

    loaded = json.loads(root_span.get_tag(IAST.JSON))
    assert get_iast_stack_trace(root_span)
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
    assert loaded["vulnerabilities"][0]["location"]["spanId"] > 0
    assert loaded["vulnerabilities"][0]["location"]["stackId"] == "1"
    assert loaded["vulnerabilities"][0]["location"]["method"] == "sqli_http_request_parameter_name_get"
    assert "class" not in loaded["vulnerabilities"][0]["location"]
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_request_parameter_name_post(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
    assert loaded["vulnerabilities"][0]["location"]["spanId"] > 0
    assert loaded["vulnerabilities"][0]["location"]["stackId"] == "1"
    assert loaded["vulnerabilities"][0]["location"]["method"] == "sqli_http_request_parameter_name_post"
    assert "class" not in loaded["vulnerabilities"][0]["location"]
    assert loaded["vulnerabilities"][0]["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_query_no_redacted(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_sqli_http_request_header_value(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_iast_disabled_sqli_http_request_header_value(client, iast_span, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
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
def test_django_sqli_http_request_header_name(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_iast_disabled_sqli_http_request_header_name(client, iast_span, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
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
def test_django_sqli_http_path_parameter(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_iast_disabled_sqli_http_path_parameter(client, iast_span, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
            tracer,
            url="/appsec/sqli_http_path_parameter/sqlite_master/",
            headers={"HTTP_USER_AGENT": "test/1.2.3"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_cookies_name(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
    assert "class" not in vulnerability["location"]
    assert vulnerability["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_cookies_name(client, iast_span, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
            tracer,
            url="/appsec/sqli_http_request_cookie_name/",
            cookies={"master": "test/1.2.3"},
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_sqli_http_cookies_value(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
    assert "class" not in vulnerability["location"]
    assert vulnerability["hash"] == hash_value


@pytest.mark.django_db()
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_iast_disabled_sqli_http_cookies_value(client, iast_span, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
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
def test_django_sqli_http_body(client, iast_span, tracer, payload, content_type):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_tainted_http_body_empty(client, iast_span, tracer, payload, content_type, deduplication, sampling):
    with override_global_config(
        dict(_iast_enabled=True, _iast_deduplication_enabled=deduplication, _iast_request_sampling=sampling)
    ):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
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
def test_django_iast_disabled_sqli_http_body(client, iast_span, tracer):
    with override_global_config(dict(_iast_enabled=False)):
        root_span, response = _aux_appsec_get_root_span(
            client,
            iast_span,
            tracer,
            url="/appsec/sqli_http_request_body/",
            payload="master",
            content_type="application/json",
        )

        assert root_span.get_tag(IAST.JSON) is None

        assert response.status_code == 200
        assert response.content == b"master"


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_querydict(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_command_injection(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
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
    assert loaded["vulnerabilities"][0]["location"]["spanId"] > 0
    assert loaded["vulnerabilities"][0]["location"]["stackId"] == "1"
    assert loaded["vulnerabilities"][0]["location"]["method"] == "command_injection"
    assert "class" not in loaded["vulnerabilities"][0]["location"]


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_command_injection_subprocess(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/command-injection-subprocess/",
        payload=urlencode({"cmd": "ls"}),
        content_type="application/x-www-form-urlencoded",
    )

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    line, hash_value = get_line_and_hash("iast_command_injection_subprocess", VULN_CMDI, filename=TEST_FILE)

    assert loaded["sources"] == [
        {"name": "cmd", "origin": "http.request.body", "value": "ls"}
    ], f'Assertion error: {loaded["sources"]}'
    assert loaded["vulnerabilities"][0]["type"] == VULN_CMDI
    assert loaded["vulnerabilities"][0]["hash"] == hash_value
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "ls", "source": 0}, {"value": " "}, {"redacted": True}]
    }, f'Assertion error: {loaded["vulnerabilities"][0]["evidence"]}'
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_command_injection_span_metrics(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/command-injection/",
        payload="master",
        content_type="application/json",
    )
    assert root_span.get_metric(IAST.ENABLED) == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".command_injection") == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SINK + ".header_injection") >= 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_body") == 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header_name") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_header") > 1.0
    assert root_span.get_metric(IAST_SPAN_TAGS.TELEMETRY_EXECUTED_SOURCE + ".http_request_path") > 1.0


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_command_injection_span_metrics_disabled(client, iast_spans_with_zero_sampling, tracer):
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
def test_django_command_injection_secure_mark(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/command-injection/secure-mark/",
        payload="master",
        content_type="application/json",
    )

    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_xss_secure_mark(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/xss/secure-mark/",
        payload='<script>alert("XSS")</script>',
        content_type="application/json",
    )

    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


@pytest.mark.parametrize(
    ("security_control", "match_function"),
    [
        (
            "SANITIZER:COMMAND_INJECTION:tests.appsec.integrations.django_tests.django_app.views:_security_control_sanitizer",
            True,
        ),
        (
            "INPUT_VALIDATOR:COMMAND_INJECTION:tests.appsec.integrations.django_tests.django_app.views:_security_control_validator:1,2",
            True,
        ),
        (
            "INPUT_VALIDATOR:COMMAND_INJECTION:tests.appsec.integrations.django_tests.django_app.views:_security_control_validator:2",
            True,
        ),
        (
            "INPUT_VALIDATOR:COMMAND_INJECTION:tests.appsec.integrations.django_tests.django_app.views:_security_control_validator:1,3,4",
            False,
        ),
        (
            "INPUT_VALIDATOR:COMMAND_INJECTION:tests.appsec.integrations.django_tests.django_app.views:_security_control_validator:1,3",
            False,
        ),
        (
            "INPUT_VALIDATOR:COMMAND_INJECTION:tests.appsec.integrations.django_tests.django_app.views:_security_control_validator",
            True,
        ),
        ("INPUT_VALIDATOR:COMMAND_INJECTION:shlex:quote;SANITIZER:XSS:html:escape", False),
    ],
)
def test_django_command_injection_security_control(client, tracer, security_control, match_function):
    with override_global_config(
        dict(
            _iast_enabled=True,
            _appsec_enabled=False,
            _iast_deduplication_enabled=False,
            _iast_request_sampling=100.0,
            _iast_security_controls=security_control,
        )
    ):
        _apply_custom_security_controls()
        span = TracerSpanContainer(tracer)
        _start_iast_context_and_oce()
        root_span, _ = _aux_appsec_get_root_span(
            client,
            span,
            tracer,
            url="/appsec/command-injection/security-control/",
            payload="master",
            content_type="application/json",
        )

        loaded = root_span.get_tag(IAST.JSON)
        if match_function:
            assert loaded is None
        else:
            assert loaded is not None
        _end_iast_context_and_oce()
        span.reset()
        _unapply_security_control()


def test_django_header_injection_secure(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/header-injection-secure/",
        payload="master",
        content_type="application/json",
    )
    if DJANGO_VERSION < (3, 2, 0):
        assert response._headers["header-injection"] == ("Header-Injection", "master")
    else:
        assert response.headers["Header-Injection"] == "master"
    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_header_injection(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/header-injection/",
        payload="master\r\nInjected-Header: 1234",
        content_type="application/json",
    )
    # Response.headers are ok in the tests, but if django call to response.serialize_headers() the result is
    # b'Content-Type: text/html; charset=utf-8\r\nHeader-Injection: master\r\nInjected-Header: 1234'
    if DJANGO_VERSION < (3, 2, 0):
        assert response._headers["header-injection"] == ("Header-Injection", "master\r\nInjected-Header: 1234")
    else:
        assert response.headers["Header-Injection"] == "master\r\nInjected-Header: 1234"
    loaded = json.loads(root_span.get_tag(IAST.JSON))

    assert loaded["sources"] == [
        {"origin": "http.request.body", "name": "http.request.body", "value": "master\r\nInjected-Header: 1234"}
    ]
    assert loaded["vulnerabilities"][0]["type"] == VULN_HEADER_INJECTION
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "Header-Injection: "}, {"value": "master\r\nInjected-Header: 1234", "source": 0}]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_unvalidated_redirect_url(client, iast_span, tracer):
    tainted_value = "http://www.malicious.com.ar.uk/muahahaha"
    root_span, _ = _aux_appsec_get_root_span(
        client, iast_span, tracer, url=f"/appsec/unvalidated_redirect_url/?url={tainted_value}"
    )

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    line, hash_value = get_line_and_hash("unvalidated_redirect_url", VULN_UNVALIDATED_REDIRECT, filename=TEST_FILE)

    assert loaded["sources"] == [
        {"origin": "http.request.parameter", "name": "url", "value": "http://www.malicious.com.ar.uk/muahahaha"}
    ]
    assert loaded["vulnerabilities"][0]["type"] == VULN_UNVALIDATED_REDIRECT
    assert loaded["vulnerabilities"][0]["hash"] == hash_value
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "http://www.malicious.com.ar.uk/muahahaha", "source": 0}]
    }
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(DJANGO_VERSION < (3, 2, 0), reason="url_has_allowed_host_and_scheme was implemented in 3.2")
@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_unvalidated_redirect_url_validator(client, iast_span, tracer):
    tainted_value = "http://www.malicious.com.ar.uk/muahahaha"
    root_span, _ = _aux_appsec_get_root_span(
        client, iast_span, tracer, url=f"/appsec/unvalidated_redirect_url_validator/?url={tainted_value}"
    )

    assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_unvalidated_redirect_url_header(client, iast_span, tracer):
    tainted_value = "http://www.malicious.com.ar.uk/muahahaha"
    root_span, _ = _aux_appsec_get_root_span(
        client, iast_span, tracer, url=f"/appsec/unvalidated_redirect_url_header/?url={tainted_value}"
    )

    loaded = json.loads(root_span.get_tag(IAST.JSON))

    assert loaded["sources"] == [
        {"origin": "http.request.parameter", "name": "url", "value": "http://www.malicious.com.ar.uk/muahahaha"}
    ]
    # Check we're only reporting
    assert len(loaded["vulnerabilities"]) == 1
    assert loaded["vulnerabilities"][0]["type"] == VULN_UNVALIDATED_REDIRECT
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "http://www.malicious.com.ar.uk/muahahaha", "source": 0}]
    }
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_unvalidated_redirect_path(client, iast_span, tracer):
    tainted_value = "muahahaha"
    root_span, _ = _aux_appsec_get_root_span(
        client, iast_span, tracer, url=f"/appsec/unvalidated_redirect_path/?url={tainted_value}"
    )

    loaded = json.loads(root_span.get_tag(IAST.JSON))
    line, hash_value = get_line_and_hash("unvalidated_redirect_path", VULN_UNVALIDATED_REDIRECT, filename=TEST_FILE)

    assert loaded["sources"] == [{"origin": "http.request.parameter", "name": "url", "value": "muahahaha"}]
    assert loaded["vulnerabilities"][0]["type"] == VULN_UNVALIDATED_REDIRECT
    assert loaded["vulnerabilities"][0]["hash"] == hash_value
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "http://localhost:8080/"}, {"value": "muahahaha", "source": 0}]
    }
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_unvalidated_redirect_safe_source_cookie(client, iast_span, tracer):
    tainted_value = "http://www.malicious.com.ar.uk/muahahaha"
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/unvalidated_redirect_safe_source_cookie/",
        cookies={"url": tainted_value},
    )

    assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_unvalidated_redirect_safe_source_header(client, iast_span, tracer):
    tainted_value = "muahahaha"
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/unvalidated_redirect_safe_source_header/",
        headers={"url": tainted_value},
    )

    assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_unvalidated_redirect_path_multiple_sources(client, iast_span, tracer):
    tainted_value = "http://www.malicious.com.ar.uk/"
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url=f"/appsec/unvalidated_redirect_path_multiple_sources/?url={tainted_value}",
        headers={"url": "muahahaha"},
    )

    loaded = json.loads(root_span.get_tag(IAST.JSON))
    line, hash_value = get_line_and_hash(
        "unvalidated_redirect_path_multiple_sources", VULN_UNVALIDATED_REDIRECT, filename=TEST_FILE
    )

    assert loaded["sources"] == [
        {"origin": "http.request.parameter", "name": "url", "value": "http://www.malicious.com.ar.uk/"},
        {"origin": "http.request.header", "name": "url", "value": "muahahaha"},
    ]
    assert loaded["vulnerabilities"][0]["type"] == VULN_UNVALIDATED_REDIRECT
    assert loaded["vulnerabilities"][0]["hash"] == hash_value
    assert loaded["vulnerabilities"][0]["evidence"] == {
        "valueParts": [{"value": "http://www.malicious.com.ar.uk/", "source": 0}, {"value": "muahahaha", "source": 1}]
    }
    assert loaded["vulnerabilities"][0]["location"]["line"] == line
    assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
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
def test_django_insecure_cookie_secure(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/insecure-cookie/test_secure/",
    )

    assert root_span.get_metric(IAST.ENABLED) == 1.0

    assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie_empty_cookie(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/insecure-cookie/test_empty_cookie/",
    )

    assert root_span.get_metric(IAST.ENABLED) == 1.0

    assert root_span.get_tag(IAST.JSON) is None


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie_2_insecure_1_secure(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/insecure-cookie/test_insecure_2_1/",
    )

    assert root_span.get_metric(IAST.ENABLED) == 1.0

    loaded = json.loads(root_span.get_tag(IAST.JSON))
    assert loaded["sources"] == []
    assert len(loaded["vulnerabilities"]) == 2


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_insecure_cookie_special_characters(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
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


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_stacktrace_leak(client, iast_span, tracer):
    root_span, _ = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/stacktrace_leak/",
    )

    assert root_span.get_metric(IAST.ENABLED) == 1.0

    loaded = json.loads(root_span.get_tag(IAST.JSON))
    assert loaded["sources"] == []
    assert len(loaded["vulnerabilities"]) == 1
    vulnerability = loaded["vulnerabilities"][0]
    assert vulnerability["type"] == VULN_STACKTRACE_LEAK
    assert vulnerability["evidence"] == {
        "valueParts": [
            {"value": 'Module: ".home.foobaruser.sources.minimal-django-example.app.py"\nException: IndexError'}
        ]
    }
    assert vulnerability["hash"]


def test_django_stacktrace_from_technical_500_response(client, iast_span, tracer, debug_mode):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/stacktrace_leak_500/",
        content_type="text/html",
    )

    assert response.status_code == 500, "Expected a 500 status code"
    assert root_span.get_metric(IAST.ENABLED) == 1.0

    loaded = json.loads(root_span.get_tag(IAST.JSON))
    # technical_500_response reports a XSS also
    vulnerability = [vln for vln in loaded["vulnerabilities"] if vln["type"] == VULN_STACKTRACE_LEAK][0]
    assert vulnerability["evidence"] == {
        "valueParts": [
            {"value": "Module: tests.appsec.integrations.django_tests.django_app.views\nException: Exception"}
        ]
    }
    assert vulnerability["hash"]


def test_django_xss(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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


def test_django_xss_safe_template_tag(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
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


def test_django_xss_autoscape(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/xss/autoscape/?input=<script>alert('XSS')</script>",
    )

    assert response.status_code == 200
    if DJANGO_VERSION > (3, 1, 0):
        assert (
            response.content
            == b"<html>\n<body>\n<p>\n    &lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;\n</p>\n</body>\n</html>\n"
        ), f"Error. content is {response.content}"
    else:
        assert (
            response.content
            == b"<html>\n<body>\n<p>\n    &lt;script&gt;alert(&#39;XSS&#39;)&lt;/script&gt;\n</p>\n</body>\n</html>\n"
        ), f"Error. content is {response.content}"
    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


def test_django_xss_secure(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/xss/secure/?input=<script>alert('XSS')</script>",
    )

    assert response.status_code == 200
    if DJANGO_VERSION > (3, 1, 0):
        assert (
            response.content
            == b"<html>\n<body>\n<p>Input: &lt;script&gt;alert(&#x27;XSS&#x27;)&lt;/script&gt;</p>\n</body>\n</html>"
        )

    else:
        assert (
            response.content
            == b"<html>\n<body>\n<p>Input: &lt;script&gt;alert(&#39;XSS&#39;)&lt;/script&gt;</p>\n</body>\n</html>"
        ), f"COntent: {response.content}"
    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


def test_django_ospathjoin_propagation(client, iast_span, tracer):
    root_span, response = _aux_appsec_get_root_span(
        client,
        iast_span,
        tracer,
        url="/appsec/propagation/ospathjoin/?input=test/propagation/errors",
    )

    assert response.status_code == 200
    assert response.content == b"OK:True:False:False", response.content

    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


@pytest.mark.django_db()
def test_django_iast_sampling(client, test_spans_2_vuln_per_request_deduplication, tracer):
    list_vulnerabilities = []
    for i in range(10):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans_2_vuln_per_request_deduplication,
            tracer,
            url=f"/appsec/iast_sampling/?param=value{i}",
        )
        assert response.status_code == 200
        assert str(response.content, encoding="utf-8") == f"OK:value{i}", response.content
        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert len(loaded["vulnerabilities"]) == 1
        assert loaded["sources"] == [
            {"origin": "http.request.parameter", "name": "param", "redacted": True, "pattern": "abcdef"}
        ]
        for vuln in loaded["vulnerabilities"]:
            assert vuln["type"] == VULN_SQL_INJECTION
            list_vulnerabilities.append(vuln["location"]["line"])
    assert (
        len(list_vulnerabilities) == 10
    ), f"Num vulnerabilities: ({len(list_vulnerabilities)}): {list_vulnerabilities}"


@pytest.mark.django_db()
def test_django_iast_sampling_2(client, test_spans_2_vuln_per_request_deduplication, tracer):
    list_vulnerabilities = []
    for i in range(10):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans_2_vuln_per_request_deduplication,
            tracer,
            url=f"/appsec/iast_sampling_2/?param=value{i}",
        )
        assert response.status_code == 200
        assert str(response.content, encoding="utf-8") == f"OK:value{i}", response.content
        if i > 0:
            assert root_span.get_tag(IAST.JSON) is None
        else:
            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert len(loaded["vulnerabilities"]) == 2
            assert loaded["sources"] == [
                {"origin": "http.request.parameter", "name": "param", "redacted": True, "pattern": "abcdef"}
            ]
            for vuln in loaded["vulnerabilities"]:
                assert vuln["type"] == VULN_SQL_INJECTION
                list_vulnerabilities.append(vuln["location"]["line"])
    assert len(list_vulnerabilities) == 2, f"Num vulnerabilities: ({len(list_vulnerabilities)}): {list_vulnerabilities}"


@pytest.mark.django_db()
def test_django_iast_sampling_by_route_method(client, test_spans_2_vuln_per_request_deduplication, tracer):
    list_vulnerabilities = []
    for i in range(10):
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans_2_vuln_per_request_deduplication,
            tracer,
            url=f"/appsec/iast_sampling_by_route_method/{i}/?param=value{i}",
        )
        assert response.status_code == 200
        assert str(response.content, encoding="utf-8") == f"OK:value{i}:{i}", response.content
        if i > 7:
            assert root_span.get_tag(IAST.JSON) is None
        else:
            loaded = json.loads(root_span.get_tag(IAST.JSON))
            assert len(loaded["vulnerabilities"]) == 2
            assert loaded["sources"] == [
                {"origin": "http.request.parameter", "name": "param", "redacted": True, "pattern": "abcdef"}
            ]
            for vuln in loaded["vulnerabilities"]:
                assert vuln["type"] == VULN_SQL_INJECTION
                list_vulnerabilities.append(vuln["location"]["line"])
    assert (
        len(list_vulnerabilities) == 16
    ), f"Num vulnerabilities: ({len(list_vulnerabilities)}): {list_vulnerabilities}"


@pytest.mark.skipif(not asm_config._iast_supported, reason="Python version not supported by IAST")
def test_django_ssrf_safe_path(client, iast_span, tracer):
    tainted_value = "path_param"
    root_span, _ = _aux_appsec_get_root_span(
        client, iast_span, tracer, url=f"/appsec/ssrf_requests/?url={tainted_value}"
    )
    loaded = root_span.get_tag(IAST.JSON)
    assert loaded is None


@pytest.mark.parametrize(
    ("option", "url", "value_parts"),
    [
        ("path", "url-path/", [{"value": "http://localhost:8080/"}, {"value": "url-path/", "source": 0}]),
        ("safe_path", "url-path/", None),
        ("protocol", "http", [{"value": "http", "source": 0}, {"value": "://localhost:8080/"}]),
        ("host", "localhost", [{"value": "http://"}, {"value": "localhost", "source": 0}, {"value": ":8080/"}]),
        ("urlencode_single", "value1", None),
        ("urlencode_multiple", "value1", None),
        ("urlencode_nested", "value1", None),
        ("urlencode_with_fragment", "value1", None),
        ("urlencode_doseq", "value1", None),
        ("safe_host", "localhost", [{"value": "http://"}, {"value": "localhost", "source": 0}, {"value": ":8080/"}]),
        ("port", "8080", [{"value": "http://localhost:"}, {"value": "8080", "source": 0}, {"value": "/"}]),
        (
            "query",
            "param1=value1&param2=value2",
            [
                {"value": "http://localhost:8080/?"},
                {"source": 0, "value": "param1="},
                {"redacted": True, "source": 0, "pattern": "hijklm"},
            ],
        ),
        (
            "query_with_fragment",
            "param1=value_with_%23hash%23&param2=value2",
            [
                {"value": "http://localhost:8080/?"},
                {"source": 0, "value": "param1="},
                {"redacted": True, "source": 0, "pattern": "hijklmnopqr"},
                {"source": 0, "value": "#hash#"},
            ],
        ),
        ("fragment1", "fragment_value1", None),
        ("fragment2", "fragment_value1", None),
        ("fragment3", "fragment_value1", None),
        ("query_param", "param1=value1&param2=value2", None),
    ],
)
def test_django_ssrf_url(client, iast_span, tracer, option, url, value_parts):
    root_span, response = _aux_appsec_get_root_span(
        client, iast_span, tracer, url=f"/appsec/ssrf_requests/?option={option}&url={url}"
    )

    assert response.status_code == 200
    assert response.content == b"OK"

    if value_parts is None:
        assert root_span.get_tag(IAST.JSON) is None
    elif option == "safe_host" and DJANGO_VERSION >= (3, 1):
        assert root_span.get_tag(IAST.JSON) is None
    else:
        loaded = json.loads(root_span.get_tag(IAST.JSON))

        line, hash_value = get_line_and_hash(f"ssrf_requests_{option}", VULN_SSRF, filename=TEST_FILE)

        assert loaded["vulnerabilities"][0]["type"] == VULN_SSRF
        assert loaded["vulnerabilities"][0]["evidence"] == {"valueParts": value_parts}
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
