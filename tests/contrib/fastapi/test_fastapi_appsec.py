import json

from fastapi import Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
import pytest

import ddtrace
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.appsec._handlers import _on_asgi_request_parse_body
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
from ddtrace.ext import http
from ddtrace.internal import constants
from ddtrace.internal import core
import tests.appsec.rules as rules
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


# IP Blocking


def fastapi_ipblock_nomatch_200_json(app, client, tracer, test_spans, ip):
    @app.get("/")
    def route():
        return "OK"

    _aux_appsec_prepare_tracer(tracer)
    for ip in [rules._IP.MONITORED, rules._IP.BYPASS, rules._IP.DEFAULT]:
        with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
            resp = client.get("/", headers={"X-Real-Ip": ip})
            root_span = get_root_span(test_spans)
            assert resp.status_code == 200
            assert not core.get_item("http.request.blocked", span=root_span)


def test_ipblock_nomatch_200_bypass(app, client, tracer, test_spans):
    fastapi_ipblock_nomatch_200_json(app, client, tracer, test_spans, rules._IP.BYPASS)


def test_ipblock_nomatch_200_monitor(app, client, tracer, test_spans):
    fastapi_ipblock_nomatch_200_json(app, client, tracer, test_spans, rules._IP.MONITORED)


def test_ipblock_nomatch_200_default(app, client, tracer, test_spans):
    fastapi_ipblock_nomatch_200_json(app, client, tracer, test_spans, rules._IP.DEFAULT)


def test_ipblock_match_403_json(app, client, tracer, test_spans):
    with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_GOOD_PATH)):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.get("/foobar", headers={"X-Real-Ip": rules._IP.BLOCKED})
        assert resp.status_code == 403
        assert get_response_body(resp) == constants.BLOCKED_RESPONSE_JSON
        root_span = get_root_span(test_spans)
        assert root_span.get_tag(http.STATUS_CODE) == "403"
        assert root_span.get_tag(http.URL) == "http://testserver/foobar"
        assert root_span.get_tag(http.METHOD) == "GET"
        assert root_span.get_tag(http.USER_AGENT) == "testclient"
        assert root_span.get_tag(SPAN_DATA_NAMES.RESPONSE_HEADERS_NO_COOKIES + ".content-type") == "text/json"
        assert root_span.get_tag(APPSEC.JSON)
        loaded = json.loads(root_span.get_tag(APPSEC.JSON))
        assert loaded["triggers"][0]["rule"]["id"] == "blk-001-001"
        assert root_span.get_tag("appsec.event") == "true"
        assert root_span.get_tag("appsec.blocked") == "true"


# Core Instrumentation


@pytest.fixture
def setup_core_ok_after_test():
    yield
    core.on("asgi.request.parse.body", _on_asgi_request_parse_body, "await_receive_and_body")


@pytest.mark.usefixtures("setup_core_ok_after_test")
def test_core_callback_request_body(app, client, tracer, test_spans):
    @app.get("/index.html")
    @app.post("/index.html")
    async def test_route(request: Request):
        body = await request._receive()
        return PlainTextResponse(body["body"])

    # test if asgi middleware is ok without any callback registered
    core.reset_listeners(event_id="asgi.request.parse.body")

    payload, content_type = '{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}', "application/json"

    with override_global_config(dict(_asm_enabled=True)), override_env(dict(DD_APPSEC_RULES=rules.RULES_SRB)):
        # disable callback
        _aux_appsec_prepare_tracer(tracer, asm_enabled=True)
        resp = client.post(
            "/index.html?args=test",
            data=payload,
            headers={"Content-Type": content_type},
        )
        assert resp.status_code == 200
        assert get_response_body(resp) == '{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}'
    with override_global_config(dict(_asm_enabled=True)):
        _aux_appsec_prepare_tracer(tracer)
        resp = client.post(
            "/index.html?args=test",
            data=payload,
            headers={"Content-Type": content_type},
        )
        assert resp.status_code == 200
        assert get_response_body(resp) == '{"attack": "yqrweytqwreasldhkuqwgervflnmlnli"}'
