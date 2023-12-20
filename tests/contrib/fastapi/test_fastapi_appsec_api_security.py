import base64
import gzip
import json

from fastapi import Request
from fastapi import Response
from fastapi.testclient import TestClient
import pytest

import ddtrace
from ddtrace.appsec._constants import API_SECURITY
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
from tests.appsec.appsec.api_security.test_schema_fuzz import equal_with_meta
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import override_env
from tests.utils import override_global_config

from . import app as fastapi_app


def _aux_appsec_prepare_tracer(tracer, asm_enabled=True):
    tracer._asm_enabled = asm_enabled
    # Hack: need to pass an argument to configure so that the processors are recreated
    tracer.configure(api_version="v0.4")


@pytest.fixture
def tracer():
    original_tracer = ddtrace.tracer
    tracer = DummyTracer()
    ddtrace.tracer = tracer
    fastapi_patch()
    yield tracer
    ddtrace.tracer = original_tracer
    fastapi_unpatch()


@pytest.fixture
def app(tracer):
    return fastapi_app.get_app()


@pytest.fixture
def client(tracer, app):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


def get_response_body(response):
    return response.text


def get_root_span(spans):
    return spans.pop_traces()[0][0]


def get_schema(root_span, name):
    value = root_span.get_tag(name)
    if value:
        return json.loads(gzip.decompress(base64.b64decode(value)).decode())
    return None


@pytest.mark.parametrize(
    ("name", "expected_value"),
    [
        (API_SECURITY.REQUEST_BODY, [{"key": [8], "ids": [[[4]], {"len": 4}]}]),
        (
            API_SECURITY.REQUEST_HEADERS_NO_COOKIES,
            [
                {
                    "content-length": [8],
                    "content-type": [8],
                    "user-agent": [8],
                    "accept-encoding": [8],
                    "connection": [8],
                    "accept": [8],
                    "host": [8],
                }
            ],
        ),
        (API_SECURITY.REQUEST_COOKIES, [{"secret": [8]}]),
        (API_SECURITY.REQUEST_QUERY, [{"extended": [[[8]], {"len": 1}], "x": [[[8]], {"len": 2}]}]),
        (API_SECURITY.REQUEST_PATH_PARAMS, [{"str_param": [8]}]),
        (
            API_SECURITY.RESPONSE_HEADERS_NO_COOKIES,
            [{"extended": [8], "x": [8], "content-type": [8], "content-length": [8]}],
        ),
        (API_SECURITY.RESPONSE_BODY, [{"ids": [[[4]], {"len": 4}], "key": [8], "validate": [2], "value": [8]}]),
    ],
)
def test_api_security(app, client, tracer, test_spans, name, expected_value):
    @app.post("/response-header-apisec/{str_param}")
    async def specific_reponse(str_param, request: Request, response: Response):
        data = await request.json()
        query_params = request.query_params
        data["validate"] = True
        data["value"] = str_param
        response.headers.update(query_params)
        return data

    payload = {"key": "secret", "ids": [0, 1, 2, 3]}

    with override_global_config(
        dict(_asm_enabled=True, _api_security_enabled=True, _api_security_sample_rate=1.0)
    ), override_env({API_SECURITY.SAMPLE_RATE: "1.0"}):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post(
            "/response-header-apisec/posting?x=2&extended=345&x=3",
            data=json.dumps(payload),
            headers={"content-type": "application/json"},
            cookies={"secret": "a1b2c3d4e5f6"},  # cookies needs to be set there for compatibility with fastapi==0.86
        )
        root_span = get_root_span(test_spans)
        assert resp.status_code == 200
        api = get_schema(root_span, name)
        assert equal_with_meta(api, expected_value), name


def test_api_security_scanners(app, client, tracer, test_spans):
    @app.post("/response-header-apisec/{str_param}")
    async def specific_reponse(str_param, request: Request, response: Response):
        data = await request.json()
        query_params = request.query_params
        data["validate"] = True
        data["value"] = str_param
        response.headers.update(query_params)
        return data

    payload = {"key": "secret", "ids": [0, 1, 2, 3]}

    with override_global_config(
        dict(_asm_enabled=True, _api_security_enabled=True, _api_security_sample_rate=1.0)
    ), override_env({API_SECURITY.SAMPLE_RATE: "1.0"}):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post(
            "/response-header-apisec/posting?x=2&extended=345&x=3",
            data=json.dumps(payload),
            cookies={
                "mastercard": "5123456789123456",
                "authorization": "digest a0b1c2",
                "SSN": "123-45-6789",
            },
            headers={
                "authorization": "digest a0b1c2",
            },
        )

        EXPECTED_COOKIES = {
            "SSN": [8, {"category": "pii", "type": "us_ssn"}],
            "authorization": [8],
            "mastercard": [
                8,
                {"card_type": "mastercard", "type": "card", "category": "payment"},
            ],
        }
        EXPECTED_HEADERS = {"authorization": [8, {"category": "credentials", "type": "digest_auth"}]}
        assert resp.status_code == 200
        root_span = get_root_span(test_spans)

        schema_cookies = get_schema(root_span, API_SECURITY.REQUEST_COOKIES)
        schema_headers = get_schema(root_span, API_SECURITY.REQUEST_HEADERS_NO_COOKIES)
        for schema, expected in [
            (schema_cookies[0], EXPECTED_COOKIES),
            (schema_headers[0], EXPECTED_HEADERS),
        ]:
            for key in expected:
                assert key in schema
                assert isinstance(schema[key], list)
                assert len(schema[key]) == len(expected[key])
                if len(schema[key]) == 2:
                    assert schema[key][1] == expected[key][1]
