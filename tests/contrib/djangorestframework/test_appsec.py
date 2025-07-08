from urllib.parse import urlencode

import django
import pytest

from ddtrace.appsec import _asm_request_context
from ddtrace.appsec._utils import get_triggers
from tests.utils import assert_span_http_status_code
from tests.utils import override_global_config


_init_finalize = _asm_request_context.finalize_asm_env
_addresses_store = []


def finalize_wrapper(env):
    _addresses_store.append(env.waf_addresses)
    _init_finalize(env)


def patch_for_waf_addresses():
    _addresses_store.clear()
    _asm_request_context.finalize_asm_env = finalize_wrapper


def unpatch_for_waf_addresses():
    _asm_request_context.finalize_asm_env = _init_finalize


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
def test_djangorest_request_body_urlencoded(client, test_spans, tracer):
    try:
        patch_for_waf_addresses()
        with override_global_config(dict(_asm_enabled=True)):
            # Hack: need to pass an argument to configure so that the processors are recreated
            tracer._recreate()
            payload = urlencode({"mytestingbody_key": "mytestingbody_value"})
            client.post("/users/", payload, content_type="application/x-www-form-urlencoded")
            root_span = test_spans.spans[0]
            assert_span_http_status_code(root_span, 500)

            query = _addresses_store[-1].get("http.request.body") if _addresses_store else None

            assert get_triggers(root_span) is None
            assert root_span.get_tag("component") == "django"
            assert root_span.get_tag("span.kind") == "server"
            assert query == {"mytestingbody_key": "mytestingbody_value"}
    finally:
        unpatch_for_waf_addresses()


@pytest.mark.skipif(django.VERSION < (1, 10), reason="requires django version >= 1.10")
def test_djangorest_request_body_custom_parser(client, test_spans, tracer):
    with override_global_config(dict(_asm_enabled=True)):
        # Hack: need to pass an argument to configure so that the processors are recreated
        tracer._recreate()
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
