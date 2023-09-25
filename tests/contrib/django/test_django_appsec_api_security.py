# -*- coding: utf-8 -*-
import base64
import gzip
import json
import sys

import pytest

from ddtrace import config
from ddtrace.appsec import _constants
from tests.appsec.api_security.test_schema_fuzz import equal_with_meta
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
    cookies=None,
):
    if cookies is None:
        cookies = {}
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
        {_constants.API_SECURITY.INTERVAL_PER_ROUTE: "0.0"}
    ):
        payload = {"key": "secret", "ids": [0, 1, 2, 3]}
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/path_param/?y=0&x=1&y=2",
            payload=payload,
            cookies={"secret": "a1b2c3d4e5f6"},
            content_type="application/json",
        )
        assert response.status_code == 200

        assert config._api_security_enabled

        headers_schema = {
            "1": [
                {
                    "content-type": [8],
                    "content-length": [8],
                    "x-frame-options": [8],
                }
            ],
            "2": [
                {
                    "content-type": [8],
                    "content-length": [8],
                    "x-frame-options": [8],
                }
            ],
            "3": [
                {
                    "content-type": [8],
                    "x-content-type-options": [8],
                    "referrer-policy": [8],
                    "x-frame-options": [8],
                    "content-length": [8],
                }
            ],
            "4": [
                {
                    "content-type": [8],
                    "cross-origin-opener-policy": [8],
                    "x-content-type-options": [8],
                    "referrer-policy": [8],
                    "x-frame-options": [8],
                    "content-length": [8],
                }
            ],
        }

        for name, expected_value in [
            ("_dd.appsec.s.req.body", [{"key": [8], "ids": [[[4]], {"len": 4}]}]),
            (
                "_dd.appsec.s.req.headers",
                [{"content-length": [8], "content-type": [8]}],
            ),
            ("_dd.appsec.s.req.cookies", [{"secret": [8]}]),
            ("_dd.appsec.s.req.query", [{"y": [8], "x": [8]}]),
            ("_dd.appsec.s.req.params", [{"year": [4], "month": [8]}]),
            ("_dd.appsec.s.res.headers", headers_schema[django.__version__[0]]),
            ("_dd.appsec.s.res.body", [{"year": [4], "month": [8]}]),
        ]:
            value = root_span.get_tag(name)
            assert value, name
            api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
            assert equal_with_meta(api, expected_value), name


@pytest.mark.skipif(sys.version_info.major < 3, reason="Python 2 not supported for api security")
def test_api_security_with_srb(client, test_spans, tracer):
    """Test if srb is still working as expected with api security activated"""

    with override_global_config(dict(_appsec_enabled=True, _api_security_enabled=True)), override_env(
        {_constants.API_SECURITY.INTERVAL_PER_ROUTE: "0.0", "DD_APPSEC_RULES": RULES_SRB}
    ):
        payload = {"key": "secret", "ids": [0, 1, 2, 3]}
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/path_param/?y=0&x=1&y=xtrace",
            payload=payload,
            cookies={"secret": "a1b2c3d4e5f6"},
            content_type="application/json",
        )
        assert response.status_code == 403
        loaded = json.loads(root_span.get_tag(_constants.APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-001"]

        assert config._api_security_enabled

        for name, expected_value in [
            ("_dd.appsec.s.req.body", [{"key": [8], "ids": [[[4]], {"len": 4}]}]),
            (
                "_dd.appsec.s.req.headers",
                [{"content-length": [8], "content-type": [8]}],
            ),
            ("_dd.appsec.s.req.cookies", [{"secret": [8]}]),
            ("_dd.appsec.s.req.query", [{"y": [8], "x": [8]}]),
            ("_dd.appsec.s.req.params", [{"year": [4], "month": [8]}]),
            ("_dd.appsec.s.res.headers", [{"content-type": [8]}]),
            ("_dd.appsec.s.res.body", [{"errors": [[[{"detail": [8], "title": [8]}]], {"len": 1}]}]),
        ]:
            value = root_span.get_tag(name)
            assert value, name
            api = json.loads(gzip.decompress(base64.b64decode(value)).decode())
            assert equal_with_meta(api, expected_value), name


@pytest.mark.skipif(sys.version_info.major < 3, reason="Python 2 not supported for api security")
def test_api_security_deactivated(client, test_spans, tracer):
    """Test if blocking is still working as expected with api security deactivated"""

    with override_global_config(dict(_appsec_enabled=True, _api_security_enabled=False)), override_env(
        {_constants.API_SECURITY.INTERVAL_PER_ROUTE: "0.0", "DD_APPSEC_RULES": RULES_SRB}
    ):
        payload = {"key": "secret", "ids": [0, 1, 2, 3]}
        root_span, response = _aux_appsec_get_root_span(
            client,
            test_spans,
            tracer,
            url="/appsec/path-params/2022/path_param/?y=0&x=1&y=xtrace",
            payload=payload,
            cookies={"secret": "a1b2c3d4e5f6"},
            content_type="application/json",
        )
        assert response.status_code == 403
        loaded = json.loads(root_span.get_tag(_constants.APPSEC.JSON))
        assert [t["rule"]["id"] for t in loaded["triggers"]] == ["tst-037-001"]

        assert not config._api_security_enabled

        for name in [
            "_dd.appsec.s.req.body",
            "_dd.appsec.s.req.headers",
            "_dd.appsec.s.req.cookies",
            "_dd.appsec.s.req.query",
            "_dd.appsec.s.req.params",
            "_dd.appsec.s.res.headers",
            "_dd.appsec.s.res.body",
        ]:
            value = root_span.get_tag(name)
            assert value is None, name
