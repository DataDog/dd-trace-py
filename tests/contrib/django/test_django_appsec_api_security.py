# -*- coding: utf-8 -*-
import base64
import gzip
import json
import sys

import pytest

from ddtrace import config
from tests.appsec.api_security.test_schema_fuzz import equal_without_meta
from tests.appsec.test_processor import RULES_SRB
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


@pytest.mark.skipif(sys.version_info.major < 3, reason="Python 2 not supported for api security")
def test_api_security(client, test_spans, tracer):
    import django

    with override_global_config(dict(_appsec_enabled=True, _api_security_enabled=True)), override_env(
        dict(DD_APPSEC_RULES=RULES_SRB)
    ):
        payload = {"key": "secret", "ids": [0, 1, 2, 3]}
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/path_param/?x=1",
            payload=payload,
            content_type="application/json",
        )
        assert response.status_code == 200

        assert config._api_security_enabled

        headers_schema = {
            "1": [
                {
                    "Content-Type": [8],
                    "Content-Length": [8],
                    "X-Frame-Options": [8],
                }
            ],
            "2": [
                {
                    "Content-Type": [8],
                    "Content-Length": [8],
                    "X-Frame-Options": [8],
                }
            ],
            "3": [
                {
                    "Content-Type": [8],
                    "X-Content-Type-Options": [8],
                    "Referrer-Policy": [8],
                    "X-Frame-Options": [8],
                    "Content-Length": [8],
                }
            ],
            "4": [
                {
                    "Content-Type": [8],
                    "Cross-Origin-Opener-Policy": [8],
                    "X-Content-Type-Options": [8],
                    "Referrer-Policy": [8],
                    "X-Frame-Options": [8],
                    "Content-Length": [8],
                }
            ],
        }

        for name, expected_value in [
            ("_dd.appsec.s.req.body", [{"key": [8], "ids": [[[4]], {"len": 4}]}]),
            (
                "_dd.appsec.s.req.headers",
                [{"Cookie": [8], "Content-Length": [8], "Content-Type": [8]}],
            ),
            ("_dd.appsec.s.req.query", [{"x": [[[8]], {"len": 1}]}]),
            ("_dd.appsec.s.req.params", [{"year": [4], "month": [8]}]),
            ("_dd.appsec.s.res.headers", headers_schema[django.__version__[0]]),
            ("_dd.appsec.s.res.body", [{"year": [4], "month": [8]}]),
        ]:
            value = root_span.get_tag(name)
            assert value, name
            api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
            assert equal_without_meta(api, expected_value)
