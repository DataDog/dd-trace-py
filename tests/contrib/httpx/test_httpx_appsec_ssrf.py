from unittest import mock

import httpx
import pytest

from ddtrace.appsec._asm_request_context import _RASP_SUBCONTEXT
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.httpx import subscribers
from ddtrace.contrib.internal.httpx.patch import patch
from ddtrace.contrib.internal.httpx.patch import unpatch
from ddtrace.internal import core


REQ = EXPLOIT_PREVENTION.TYPE.SSRF_REQ
RES = EXPLOIT_PREVENTION.TYPE.SSRF_RES


def _handler(request):
    if request.url.path == "/source":
        return httpx.Response(302, headers={"Location": "/target"})
    return httpx.Response(200, json={})


TRANSPORT = httpx.MockTransport(_handler)


@pytest.fixture
def ssrf_calls():
    calls = []

    def record(addresses, rule_type=None, **kwargs):
        url = addresses.get(EXPLOIT_PREVENTION.ADDRESS.SSRF)
        calls.append((rule_type, url and httpx.URL(url).path, core.find_item(_RASP_SUBCONTEXT)))

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


def _assert_nested_request_isolated(calls, nested_first):
    nested = [(REQ, "/nested"), (RES, None)]
    outer = [(REQ, "/outer"), (RES, None)]
    expected = nested + outer if nested_first else outer[:1] + nested + outer[1:]
    assert [(rule_type, path) for rule_type, path, _ in calls] == expected

    holders = [holder for _, _, holder in calls]
    (n1, n2), (o1, o2) = ((0, 1), (2, 3)) if nested_first else ((1, 2), (0, 3))
    assert holders[n1] is holders[n2] is not None
    assert holders[o1] is holders[o2] is not None
    assert holders[n1] is not holders[o1]


def test_a_request_from_an_event_hook_gets_its_own_rasp_subcontext(ssrf_calls):
    def hook(response):
        if response.request.url.path == "/outer":
            client.get("http://example.test/nested")

    with httpx.Client(transport=TRANSPORT, event_hooks={"response": [hook]}) as client:
        client.get("http://example.test/outer")

    _assert_nested_request_isolated(ssrf_calls, nested_first=False)


@pytest.mark.asyncio
async def test_a_request_from_an_async_event_hook_gets_its_own_rasp_subcontext(ssrf_calls):
    async def hook(response):
        if response.request.url.path == "/outer":
            await client.get("http://example.test/nested")

    async with httpx.AsyncClient(transport=TRANSPORT, event_hooks={"response": [hook]}) as client:
        await client.get("http://example.test/outer")

    _assert_nested_request_isolated(ssrf_calls, nested_first=False)


def test_a_request_from_an_auth_flow_gets_its_own_rasp_subcontext(ssrf_calls):
    class TokenAuth(httpx.Auth):
        def auth_flow(self, request):
            with httpx.Client(transport=TRANSPORT) as token_client:
                token_client.get("http://example.test/nested")
            yield request

    with httpx.Client(transport=TRANSPORT, auth=TokenAuth()) as client:
        client.get("http://example.test/outer")

    _assert_nested_request_isolated(ssrf_calls, nested_first=True)


def test_redirect_hops_share_the_request_rasp_subcontext(ssrf_calls):
    with httpx.Client(transport=TRANSPORT, follow_redirects=True) as client:
        client.get("http://example.test/source")

    assert [(rule_type, path) for rule_type, path, _ in ssrf_calls] == [
        (REQ, "/source"),
        (RES, None),
        (REQ, "/target"),
        (RES, None),
    ]
    assert len({id(holder) for _, _, holder in ssrf_calls}) == 1
    assert ssrf_calls[0][2] is not None
