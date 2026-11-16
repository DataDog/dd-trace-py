from unittest import mock

import httpx
import pytest

from ddtrace.appsec._asm_request_context import _RASP_SUBCONTEXT
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.httpx import subscribers
from ddtrace.contrib.internal.httpx.patch import patch
from ddtrace.contrib.internal.httpx.patch import unpatch
from ddtrace.internal import core
from tests.appsec.utils import assert_nested_request_isolated


NESTED = "http://example.test/nested"
TRANSPORT = httpx.MockTransport(
    lambda request: httpx.Response(302, headers={"Location": "/target"})
    if request.url.path == "/source"
    else httpx.Response(200, json={})
)


@pytest.fixture
def ssrf_calls():
    calls = []

    def record(addresses, rule_type=None, **kwargs):
        url = addresses.get(EXPLOIT_PREVENTION.ADDRESS.SSRF)
        calls.append((rule_type, url and str(url), core.find_item(_RASP_SUBCONTEXT)))

    patch()
    subscribers.AppSecHttpxRequestContextSubscriber.register()
    subscribers.AppSecHttpxSingleRequestContextSubscriber.register()
    try:
        with (
            mock.patch.object(subscribers, "get_rasp_capability", return_value=True),
            mock.patch.object(subscribers, "_get_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(subscribers, "should_analyze_body_response", return_value=False),
            mock.patch.object(subscribers, "get_blocked", return_value=None),
            mock.patch.object(subscribers, "call_waf_callback", side_effect=record),
        ):
            yield calls
    finally:
        subscribers.AppSecHttpxSingleRequestContextSubscriber.unregister()
        subscribers.AppSecHttpxRequestContextSubscriber.unregister()
        unpatch()


def _get_nested():
    with httpx.Client(transport=TRANSPORT) as client:
        client.get(NESTED)


def _hooks(path):
    def hook(response):
        if response.request.url.path == path:
            _get_nested()

    return {"response": [hook]}


@pytest.mark.parametrize(
    "client_kwargs,url",
    [
        ({"event_hooks": _hooks("/outer")}, "http://example.test/outer"),
        ({"auth": lambda request: _get_nested() or request}, "http://example.test/outer"),
        # The redirect hops share the outer request's subcontext.
        ({"event_hooks": _hooks("/target"), "follow_redirects": True}, "http://example.test/source"),
    ],
    ids=["event_hook", "auth_flow", "redirect"],
)
def test_a_request_nested_in_an_httpx_request_gets_its_own_rasp_subcontext(ssrf_calls, client_kwargs, url):
    with httpx.Client(transport=TRANSPORT, **client_kwargs) as client:
        client.get(url)

    assert_nested_request_isolated(ssrf_calls, NESTED)


@pytest.mark.asyncio
async def test_a_request_nested_in_an_async_httpx_request_gets_its_own_rasp_subcontext(ssrf_calls):
    async def hook(response):
        if response.request.url.path == "/outer":
            await client.get(NESTED)

    async with httpx.AsyncClient(transport=TRANSPORT, event_hooks={"response": [hook]}) as client:
        await client.get("http://example.test/outer")

    assert_nested_request_isolated(ssrf_calls, NESTED)
