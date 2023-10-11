import json

from fastapi.testclient import TestClient
import pytest

import ddtrace
from ddtrace.appsec._constants import APPSEC
from ddtrace.appsec._constants import SPAN_DATA_NAMES
from ddtrace.contrib.fastapi import patch as fastapi_patch
from ddtrace.contrib.fastapi import unpatch as fastapi_unpatch
from ddtrace.ext import http
from ddtrace.internal import constants
from ddtrace.internal import core
from tests.appsec.test_processor import RULES_GOOD_PATH
from tests.appsec.test_processor import _IP
from tests.utils import DummyTracer
from tests.utils import TracerSpanContainer
from tests.utils import override_env
from tests.utils import override_global_config

from . import app


_app = app.get_app()


def _aux_appsec_prepare_tracer(tracer, appsec_enabled=True):
    tracer._appsec_enabled = appsec_enabled
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
def client(tracer):
    with TestClient(app.get_app()) as test_client:
        yield test_client


@pytest.fixture
def test_spans(tracer):
    container = TracerSpanContainer(tracer)
    yield container
    container.reset()


def get_response_body(response):
    if hasattr(response, "text"):
        return response.text
    return response.data.decode("utf-8")


def fastapi_ipblock_nomatch_200_json(client, tracer, test_spans, ip):
    @_app.get("/")
    def route():
        return "OK"

    _aux_appsec_prepare_tracer(tracer)
    for ip in [_IP.MONITORED, _IP.BYPASS, _IP.DEFAULT]:
        with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
            resp = client.get("/", headers={"X-Real-Ip": ip})
            root_span = test_spans.pop_traces()[0][0]
            assert resp.status_code == 200
            assert not core.get_item("http.request.blocked", span=root_span)


def test_fastapi_ipblock_nomatch_200_bypass(client, tracer, test_spans):
    fastapi_ipblock_nomatch_200_json(client, tracer, test_spans, _IP.BYPASS)


def test_fastapi_ipblock_nomatch_200_monitor(client, tracer, test_spans):
    fastapi_ipblock_nomatch_200_json(client, tracer, test_spans, _IP.MONITORED)


def test_fastapi_ipblock_nomatch_200_default(client, tracer, test_spans):
    fastapi_ipblock_nomatch_200_json(client, tracer, test_spans, _IP.DEFAULT)


def test_fastapi_ipblock_match_403_json(client, tracer, test_spans):
    with override_global_config(dict(_appsec_enabled=True)), override_env(dict(DD_APPSEC_RULES=RULES_GOOD_PATH)):
        _aux_appsec_prepare_tracer(tracer)
        print("\nTEST_START")
        resp = client.get("/foobar", headers={"X-Real-Ip": _IP.BLOCKED})
        assert resp.status_code == 403
        assert get_response_body(resp) == constants.BLOCKED_RESPONSE_JSON
        root_span = test_spans.pop_traces()[0][0]
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
