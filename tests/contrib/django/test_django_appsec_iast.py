# -*- coding: utf-8 -*-
import json
import re
import zlib

import mock
import pytest

from ddtrace import config
from ddtrace._monkey import patch_iast
from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._util import _is_python_version_supported as python_supported_by_iast
from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import urlencode
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


TEST_FILE = "tests/contrib/django/django_app/appsec_urls.py"


def get_line(label, filename=TEST_FILE):
    """get the line number after the label comment in source file `filename`"""
    with open(filename, "r") as file_in:
        for nb_line, line in enumerate(file_in):
            if re.search("label " + re.escape(label), line):
                return nb_line + 2
    assert False, "label %s not found" % label


def get_line_and_hash(label, vuln_type, filename=TEST_FILE):
    """return the line number and the associated vulnerability hash for `label` and source file `filename`"""
    line = get_line(label, filename=filename)
    rep = "Vulnerability(type='%s', location=Location(path='%s', line=%d))" % (vuln_type, filename, line)
    hash_value = zlib.crc32(rep.encode())
    if PY2 and hash_value < 0:
        hash_value += 1 << 32

    return line, hash_value


def test_django_weak_hash(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        oce.reconfigure()
        patch_iast(weak_hash=True)
        root_span, _ = _aux_appsec_get_root_span(client, test_spans, tracer, url="/appsec/weak-hash/")
        str_json = root_span.get_tag(IAST.JSON)
        assert str_json is not None, "no JSON tag in root span"
        vulnerability = json.loads(str_json)["vulnerabilities"][0]
        assert vulnerability["location"]["path"].endswith(TEST_FILE)
        assert vulnerability["evidence"]["value"] == "md5"


@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled(client, test_spans, tracer):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=True)):
        oce.reconfigure()
        tracer._iast_enabled = True
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

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
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=False)):
        oce.reconfigure()
        clear_taint_mapping()
        setup(bytes.join, bytearray.join)

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
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=True)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=True
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

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
        line, hash_value = get_line_and_hash("iast_enabled_sqli_http_request_parameter", vuln_type)
        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == [
            {"origin": "http.request.parameter", "name": "q", "value": "SELECT 1 FROM sqlite_master"}
        ]
        assert loaded["vulnerabilities"][0]["type"] == vuln_type
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "SELECT 1 FROM sqlite_master", "source": 0}]
        }
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_enabled_sqli_http_request_header_value(client, test_spans, tracer):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=True)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=True
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/sqli_http_request_header_value/",
            headers={"HTTP_USER_AGENT": "master"},
        )

        vuln_type = "SQL_INJECTION"
        line, hash_value = get_line_and_hash("iast_enabled_sqli_http_request_header_value", vuln_type)
        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == [{"origin": "http.request.header", "name": "HTTP_USER_AGENT", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == vuln_type
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "SELECT 1 FROM sqlite_"}, {"source": 0, "value": "master"}]
        }
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line

        assert response.status_code == 200
        assert response.content == b"master"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_disabled_sqli_http_request_header_value(client, test_spans, tracer):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=False)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=False
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

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
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=True)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=True
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            payload=urlencode({"mytestingbody_key": "mytestingbody_value"}),
            content_type="application/x-www-form-urlencoded",
            url="/appsec/sqli_http_request_header_name/",
            headers={"master": "test/1.2.3"},
        )

        vuln_type = "SQL_INJECTION"
        line, hash_value = get_line_and_hash("iast_enabled_sqli_http_request_header_name", vuln_type)
        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == [{"origin": "http.request.header.name", "name": "master", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == vuln_type
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "SELECT 1 FROM sqlite_"}, {"source": 0, "value": "master"}]
        }
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_user_agent_iast_disabled_sqli_http_request_header_name(client, test_spans, tracer):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=False)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=True
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

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
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=True)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=True
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_path_parameter/sqlite_master/",
            headers={"HTTP_USER_AGENT": "test/1.2.3"},
        )
        vuln_type = "SQL_INJECTION"
        line, hash_value = get_line_and_hash("iast_enabled_full_sqli_http_path_parameter", vuln_type)

        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == [
            {"origin": "http.request.path.parameter", "name": "q_http_path_parameter", "value": "sqlite_master"}
        ]
        assert loaded["vulnerabilities"][0]["type"] == vuln_type
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "SELECT 1 from "}, {"value": "sqlite_master", "source": 0}]
        }
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_iast_disabled_full_sqli_http_path_parameter(client, test_spans, tracer):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=False)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=False
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

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
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=True)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=True
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_cookie_name/",
            cookies={"master": "test/1.2.3"},
        )
        vuln_type = "SQL_INJECTION"
        line, hash_value = get_line_and_hash("iast_enabled_sqli_http_cookies_name", vuln_type)

        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == [{"origin": "http.request.cookie.name", "name": "master", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == vuln_type
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "SELECT 1 FROM sqlite_"}, {"source": 0, "value": "master"}]
        }
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE
        assert loaded["vulnerabilities"][0]["location"]["line"] == line

        assert response.status_code == 200
        assert response.content == b"test/1.2.3"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_iast_disabled_sqli_http_cookies_name(client, test_spans, tracer):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=False)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=False
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

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
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=True)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=True
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/sqli_http_request_cookie_value/",
            cookies={"master": "master"},
        )
        vuln_type = "SQL_INJECTION"
        line, hash_value = get_line_and_hash("iast_enabled_sqli_http_cookies_value", vuln_type)
        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == [{"origin": "http.request.cookie.value", "name": "master", "value": "master"}]
        assert loaded["vulnerabilities"][0]["type"] == "SQL_INJECTION"
        assert loaded["vulnerabilities"][0]["hash"] == hash_value
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"value": "SELECT 1 FROM sqlite_"}, {"source": 0, "value": "master"}]
        }
        assert loaded["vulnerabilities"][0]["location"]["line"] == line
        assert loaded["vulnerabilities"][0]["location"]["path"] == TEST_FILE

        assert response.status_code == 200
        assert response.content == b"master"


@pytest.mark.django_db()
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_django_tainted_iast_disabled_sqli_http_cookies_value(client, test_spans, tracer):
    from ddtrace.appsec.iast._taint_dict import clear_taint_mapping
    from ddtrace.appsec.iast._taint_tracking import setup

    with override_global_config(dict(_iast_enabled=False)), mock.patch(
        "ddtrace.contrib.dbapi._is_iast_enabled", return_value=False
    ):
        setup(bytes.join, bytearray.join)
        clear_taint_mapping()

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
