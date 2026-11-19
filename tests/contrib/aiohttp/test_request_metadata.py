from unittest.mock import patch

from aiohttp import web
import pytest

from ddtrace.aiguard import Message
from ddtrace.aiguard import new_ai_guard_client
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.aiguard._initialization import load_ai_guard
from ddtrace.contrib.internal.aiohttp.middlewares import trace_app
from ddtrace.ext import http
from ddtrace.ext import net
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings.asm import config as asm_config
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config
from tests.utils import override_global_config


HEADERS = {"X-Forwarded-For": "8.8.8.8", "User-Agent": "aiohttp-test-agent"}

AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-app-key",
)


@pytest.fixture
def ai_guard_enabled():
    with override_ai_guard_config(AI_GUARD_CONFIG):
        load_ai_guard()
        yield


def _application(handler):
    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    return app


def _finished_spans(test_spans, *names):
    spans = [span for trace in test_spans.pop_traces() for span in trace]
    return tuple([span for span in spans if span.name == name] for name in names)


@pytest.mark.parametrize(
    "otel_semantics,tag_names",
    [
        (False, (http.USER_AGENT, http.CLIENT_IP, net.CLIENT_IP)),
        (True, (otel_http.USER_AGENT_ORIGINAL, otel_http.CLIENT_ADDRESS, otel_http.NETWORK_PEER_ADDRESS)),
    ],
)
async def test_client_address_is_tagged_before_the_handler_runs(
    otel_semantics, tag_names, tracer, test_spans, aiohttp_client, request_completed
):
    expected = dict(zip(tag_names, ("aiohttp-test-agent", "8.8.8.8", "127.0.0.1")))
    tags_in_handler = {}

    async def handler(request):
        root_span = tracer.current_root_span()
        tags_in_handler.update({tag: root_span.get_tag(tag) for tag in tag_names})
        return web.Response(text="ok")

    with override_global_config(dict(_retrieve_client_ip=True, _otel_trace_semantics_enabled=otel_semantics)):
        client = await aiohttp_client(_application(handler))
        response = await client.get("/", headers=HEADERS)
        assert response.status == 200
        await response.text()
        await request_completed()

    assert tags_in_handler == expected
    ((request_span,),) = _finished_spans(test_spans, "aiohttp.request")
    assert {tag: request_span.get_tag(tag) for tag in tag_names} == expected


@patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
async def test_ai_guard_event_reports_the_client_address_without_appsec(
    mock_execute_request, test_spans, aiohttp_client, request_completed, ai_guard_enabled
):
    assert not asm_config._asm_enabled
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    async def handler(request):
        new_ai_guard_client().evaluate([Message(role="user", content="hello")])
        return web.Response(text="ok")

    client = await aiohttp_client(_application(handler))
    response = await client.get("/", headers=HEADERS)
    assert response.status == 200
    await response.text()
    await request_completed()

    (request_span,), (ai_guard_span,) = _finished_spans(test_spans, "aiohttp.request", AI_GUARD.RESOURCE_TYPE)
    assert request_span.get_tag(AI_GUARD.EVENT_TAG) == "true"
    assert request_span.get_tag(http.CLIENT_IP) == "8.8.8.8"
    assert request_span.get_tag(net.CLIENT_IP) == "127.0.0.1"
    assert ai_guard_span.get_tag(f"{AI_GUARD.TAG}.{http.USER_AGENT}") == "aiohttp-test-agent"
    assert ai_guard_span.get_tag(f"{AI_GUARD.TAG}.{http.CLIENT_IP}") == "8.8.8.8"
    assert ai_guard_span.get_tag(f"{AI_GUARD.TAG}.{net.CLIENT_IP}") == "127.0.0.1"
