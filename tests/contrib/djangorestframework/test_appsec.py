import django
import pytest

from ddtrace.appsec._utils import get_triggers
from ddtrace.internal import core
from ddtrace.internal.compat import urlencode
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
def test_djangorest_request_body_urlencoded(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)):
        tracer._asm_enabled = True
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer.configure(api_version="v0.4")
        payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
        client.post("/users/", payload, content_type="application/x-www-form-urlencoded")
        root_span = test_spans.spans[0]
        assert_span_http_status_code(root_span, 500)
        query = dict(core.get_item("http.request.body", span=root_span))

        assert get_triggers(root_span) is None
        assert root_span.get_tag("component") == "django"
        assert root_span.get_tag("span.kind") == "server"
        assert query == {"mytestingbody_key": "mytestingbody_value"}


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
def test_djangorest_request_body_custom_parser(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)):
        tracer._asm_enabled = True
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

        assert get_triggers(root_span) is None
        assert root_span.get_tag("component") == "django"
        assert root_span.get_tag("span.kind") == "server"
        # check that the custom parser was used to parse the body
        assert request.content == b'{"received data form":{"yqrweytqwreasldhkuqwgervflnmlnli":"Content of a.txt.\\n"}}'
