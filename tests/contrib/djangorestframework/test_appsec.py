import json

import django
import pytest

from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast import oce
from ddtrace.appsec.iast._util import _is_python_version_supported as python_supported_by_iast
from ddtrace.internal import _context
from ddtrace.internal.compat import urlencode
from tests.utils import assert_span_http_status_code
from tests.utils import override_env
from tests.utils import override_global_config


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
def test_djangorest_request_body_urlencoded(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        client.post("/users/", payload, content_type="application/x-www-form-urlencoded")
        root_span = test_spans.spans[0]
        assert_span_http_status_code(root_span, 500)
        query = dict(_context.get_item("http.request.body", span=root_span))

        assert root_span.get_tag("_dd.appsec.json") is None
        assert root_span.get_tag("component") == "django"
        assert root_span.get_tag("span.kind") == "server"
        assert query == {"mytestingbody_key": "mytestingbody_value"}


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
def test_djangorest_request_body_custom_parser(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True)):
        tracer._appsec_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload, content_type = (
            '--52d1fb4eb9c021e53ac2846190e4ac72\r\nContent-Disposition: form-data; name="json"\r\n'
            'Content-Type: application/json\r\n\r\n{"value": "yqrweytqwreasldhkuqwgervflnmlnli"}\r\n'
            "--52d1fb4eb9c021e53ac2846190e4ac72\r\n"
            'Content-Disposition: form-data; name="file1"; filename="a.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
            "Content of a.txt.\n\r\n"
            "--52d1fb4eb9c021e53ac2846190e4ac72--\r\n",
            "multipart/form-data; boundary=52d1fb4eb9c021e53ac2846190e4ac72",
        )

        request = client.post("/asm/", payload, content_type=content_type)
        root_span = test_spans.get_root_span()
        assert_span_http_status_code(root_span, 200)

        assert root_span.get_tag("_dd.appsec.json") is None
        assert root_span.get_tag("component") == "django"
        assert root_span.get_tag("span.kind") == "server"
        # check that the custom parser was used to parse the body
        assert request.content == b'{"received data form":{"yqrweytqwreasldhkuqwgervflnmlnli":"Content of a.txt.\\n"}}'


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.django_db
def test_djangorest_iast_json(client, test_spans, tracer):
    with override_global_config(
        dict(
            _appsec_enabled=True,
            _iast_enabled=True,
        )
    ), override_env({"DD_IAST_REQUEST_SAMPLING": "100"}):
        oce.reconfigure()
        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        tracer._appsec_enabled = True
        tracer._iast_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")

        payload = {"query": "SELECT * FROM auth_user"}
        request = client.post("/iast/sqli/", payload)

        root_span = test_spans.get_root_span()
        assert_span_http_status_code(root_span, 200)

        assert root_span.get_metric(IAST.ENABLED) == 1.0

        loaded = json.loads(root_span.get_tag(IAST.JSON))
        assert loaded["sources"] == [
            {"origin": "http.request.parameter", "name": "query", "value": "SELECT * FROM auth_user"}
        ]
        assert loaded["vulnerabilities"][0]["type"] == "SQL_INJECTION"
        assert loaded["vulnerabilities"][0]["evidence"] == {
            "valueParts": [{"source": 0, "value": "SELECT * FROM auth_user"}]
        }
        assert loaded["vulnerabilities"][0]["location"]["path"] == "tests/contrib/djangorestframework/app/views.py"
        assert loaded["vulnerabilities"][0]["location"]["line"] == 84

        assert request.content == b'{"received sqli data":{"query":"SELECT * FROM auth_user"}}'


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
@pytest.mark.django_db
def test_djangorest_iast_json_complex_payload(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        tracer._appsec_enabled = True
        tracer._iast_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")

        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)

        # TODO: IAST isn't taint request.data because it fails with DRF + customparsers
        # payload = {"query": {"data1": {"data2": {"data3": {"data4": "SELECT * FROM auth_user"}}}}}
        # request = client.post("/iast/sqli_complex_payload/", json.dumps(payload), content_type="application/json")
        #
        # root_span = test_spans.get_root_span()
        # assert_span_http_status_code(root_span, 200)
        #
        # assert root_span.get_metric(IAST.ENABLED) == 1.0

        # loaded = json.loads(root_span.get_tag(IAST.JSON))
        # assert loaded["sources"] == [{"name": "query", "origin": "http.request.parameter", "value": "data1"}]
        # assert loaded["vulnerabilities"][0]["type"] == "SQL_INJECTION"
        # assert loaded["vulnerabilities"][0]["evidence"] == {"valueParts": [{"source": 0, "value": "data1"}]}
        # assert loaded["vulnerabilities"][0]["location"]["path"] == "tests/contrib/djangorestframework/app/views.py"
        # assert loaded["vulnerabilities"][0]["location"]["line"] == 83
        #
        # assert request.content == b'{"received sqli data":{"query":"SELECT * FROM auth_user"}}'


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
@pytest.mark.skipif(not python_supported_by_iast(), reason="Python version not supported by IAST")
def test_djangorest_iast_custom_parser(client, test_spans, tracer):
    with override_global_config(dict(_appsec_enabled=True, _iast_enabled=True)):
        tracer._appsec_enabled = True
        tracer._iast_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")

        from ddtrace.appsec.iast._taint_tracking import setup

        setup(bytes.join, bytearray.join)
        # TODO: IAST fails with DRF + customparsers
        # request = client.post("/asm/")
        #
        # root_span = test_spans.get_root_span()
        # assert_span_http_status_code(root_span, 200)
