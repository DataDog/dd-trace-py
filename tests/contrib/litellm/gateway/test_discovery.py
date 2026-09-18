"""Discovery uses provider-shaped responses, never real keys or management APIs."""

import asyncio
import json
from unittest import mock

import httpx
import pytest

from ddtrace.contrib.internal.litellm import _gateway_discovery as discovery
from tests.contrib.litellm.test_gateway import finish
from tests.contrib.litellm.test_gateway import make_callback
from tests.contrib.litellm.test_gateway import start


SECRET = "sk-ant-api03-SYNTHETIC-PRIVATE-abcdef"
HINT = "sk-ant-api03-SYN...cdef"
ADMIN = "sk-ant-admin-SYNTHETIC-PRIVATE"


def page(*rows, more=False, last=None):
    return {"data": list(rows), "has_more": more, "last_id": last}


@pytest.fixture
def inventory(monkeypatch):
    routes = {}
    requests = []

    async def respond(request):
        requests.append(request)
        result = routes[(request.url.host, request.url.raw_path.decode())]
        if isinstance(result, Exception):
            raise result
        if callable(result):
            result = await result(request)
        status, body = result if isinstance(result, tuple) else (200, result)
        return httpx.Response(status, stream=httpx.ByteStream(json.dumps(body).encode()))

    def transport(**kwargs):
        assert kwargs == {"retries": 0, "trust_env": False}
        return httpx.MockTransport(respond)

    monkeypatch.setattr(discovery.httpx, "AsyncHTTPTransport", transport)
    monkeypatch.setenv("TEST_ADMIN_KEY", ADMIN)
    return routes, requests


def resolver(provider="anthropic"):
    return discovery.ProviderKeyDiscovery({provider: "TEST_ADMIN_KEY"})


@pytest.mark.parametrize(
    "hint,matched",
    [
        (HINT, True),
        (HINT.replace("...", "…"), True),
        (HINT.replace("...", "****"), True),
        ("...cdef", False),
        ("sk-ant...", False),
        ("sk-ant...ef", False),
        ("sk-.*...cdef", False),
        ("sk-ant...WRONG", False),
        (None, False),
        (SECRET, False),
        ({"hint": HINT}, False),
        ("a" * 4097, False),
    ],
)
def test_hint_match(hint, matched):
    assert discovery._hint_matches(SECRET, hint) is matched


@pytest.mark.parametrize(
    "providers",
    [
        False,
        [],
        "anthropic",
        {"anthropic": "literal secret"},
        {"anthropic": 123},
        {"anthropic": ""},
        {"unknown": "KEY"},
    ],
)
def test_invalid_config(providers):
    with pytest.raises(ValueError):
        discovery.ProviderKeyDiscovery(providers)


def test_disabled_and_native_endpoint_capture():
    route = {"ai.route.provider": "anthropic", "ai.route.endpoint_host": "api.anthropic.com"}
    assert discovery.ProviderKeyDiscovery().capture({"api_key": SECRET}, route) is None
    request = resolver().capture({"api_key": SECRET}, route)
    assert request.secret == SECRET
    assert SECRET not in repr(request)
    for host in ("evil.test", "api.anthropic.com.evil.test", "", "api.openai.com"):
        foreign = dict(route, **{"ai.route.endpoint_host": host})
        assert resolver().capture({"api_key": SECRET}, foreign) is None
        assert foreign["ai.discovery.status"] == "unsupported_endpoint"
    assert resolver().capture({"api_key": "os.environ/CLIENT_CONTROLLED"}, route) is None
    assert route["ai.discovery.status"] == "missing_api_key"


@pytest.mark.parametrize(
    "provider,host,header,value",
    [
        ("anthropic", "api.anthropic.com", "X-API-Key", SECRET),
        ("anthropic", "api.anthropic.com", "Authorization", "Bearer " + SECRET),
        ("openai", "eu.api.openai.com", "Authorization", "Bearer " + SECRET),
        ("gemini", "generativelanguage.googleapis.com", "X-Goog-Api-Key", "AIzaSYNTHETIC"),
    ],
)
def test_uses_outgoing_auth_header_not_original_api_key(provider, host, header, value):
    route = {"ai.route.provider": provider, "ai.route.endpoint_host": host}
    request = resolver(provider).capture({"api_key": "sk-overridden"}, route, {header: value})
    assert request.secret == value.removeprefix("Bearer ")


@pytest.mark.parametrize(
    "headers",
    [
        {"X-API-Key": SECRET, "x-api-key": "sk-conflict"},
        {"x-api-key": None},
        {"Authorization": "Basic private"},
        {"x-api-key": "os.environ/SECRET"},
        {str(i): "ignored" for i in range(129)},
    ],
)
def test_conflicting_invalid_or_non_key_headers_do_not_fall_back_to_argument(headers):
    route = {"ai.route.provider": "anthropic", "ai.route.endpoint_host": "api.anthropic.com"}
    assert resolver().capture({"api_key": SECRET}, route, headers) is None


@pytest.mark.asyncio
async def test_anthropic_discovery_pagination_scope_and_secret_safety(inventory):
    routes, calls = inventory
    routes["api.anthropic.com", "/v1/organizations/me"] = {"id": "org-verified"}
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = page(
        {"id": "apikey_other", "partial_key_hint": "sk-ant-NOT...cdef"}, more=True, last="apikey_other"
    )
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100&after_id=apikey_other"] = page(
        {
            "id": "apikey_found",
            "partial_key_hint": HINT,
            "scope": {"type": "workspace", "workspace_id": "wrkspc-verified"},
            "secret": SECRET,
        }
    )
    result = await resolver().resolve(
        discovery.KeyRequest("anthropic", SECRET),
        {
            "ai.response.anthropic_organization_id": "org-verified",
            "ai.response.anthropic_workspace_id": "wrkspc-verified",
        },
    )
    assert result == {
        "ai.route.api_key_id": "apikey_found",
        "ai.route.api_key_id_source": "unique_key_hint",
        "ai.discovery.status": "discovered",
    }
    assert all(c.headers["x-api-key"] == ADMIN for c in calls)
    assert all(c.method == "GET" and SECRET not in str(c.url) for c in calls)
    assert not any(s in json.dumps(result) for s in (SECRET, ADMIN, HINT))


@pytest.mark.asyncio
async def test_openai_discovers_project_and_key(inventory):
    routes, calls = inventory
    routes["api.openai.com", "/v1/organization/projects?limit=100&include_archived=true"] = page(
        {"id": "proj_first"}, more=True, last="proj_first"
    )
    routes["api.openai.com", "/v1/organization/projects?limit=100&include_archived=true&after=proj_first"] = page(
        {"id": "proj_second"}
    )
    routes["api.openai.com", "/v1/organization/projects/proj_first/api_keys?limit=100&owner_project_access=any"] = (
        page()
    )
    routes["api.openai.com", "/v1/organization/projects/proj_second/api_keys?limit=100&owner_project_access=any"] = (
        page({"id": "key_found", "redacted_value": HINT})
    )
    result = await resolver("openai").resolve(
        discovery.KeyRequest("openai", SECRET), {"ai.response.openai_organization": "org-native"}
    )
    assert result["ai.route.api_key_id"] == "key_found"
    assert result["ai.route.project"] == "proj_second"
    assert all(c.headers["Authorization"] == "Bearer " + ADMIN for c in calls)
    assert all(c.headers["OpenAI-Organization"] == "org-native" for c in calls)


@pytest.mark.asyncio
async def test_openai_uses_observed_project(inventory):
    routes, calls = inventory
    routes["api.openai.com", "/v1/organization/projects/proj_observed/api_keys?limit=100&owner_project_access=any"] = (
        page({"id": "key_found", "redacted_value": HINT})
    )
    result = await resolver("openai").resolve(
        discovery.KeyRequest("openai", SECRET), {"ai.response.openai_project": "proj_observed"}
    )
    assert result["ai.route.project"] == "proj_observed"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_gemini_exact_lookup_returns_key_and_project_number(inventory):
    routes, calls = inventory
    secret = "AIzaSYNTHETIC-PRIVATE"
    name = "projects/123456789/locations/global/keys/key-uuid"
    routes["apikeys.googleapis.com", "/v2/keys:lookupKey?keyString=" + secret] = {
        "name": name,
        "parent": "projects/123456789/locations/global",
    }
    routes["cloudresourcemanager.googleapis.com", "/v3/projects/123456789"] = {
        "name": "projects/123456789",
        "projectId": "customer-project",
    }
    client = resolver("gemini")
    request = client.capture(
        {"api_key": secret},
        {"ai.route.provider": "gemini", "ai.route.endpoint_host": "generativelanguage.googleapis.com"},
    )
    assert request is not None
    result = await client.resolve(request, {})
    assert result == {
        "ai.route.api_key_id": "key-uuid",
        "ai.route.api_key_resource_name": name,
        "ai.route.project_number": "123456789",
        "ai.route.api_key_id_source": "provider_lookup",
        "ai.route.project": "customer-project",
        "ai.discovery.status": "discovered",
    }
    assert calls[0].headers["Authorization"] == "Bearer " + ADMIN
    assert secret not in repr(result) and secret not in repr(client._cache)


@pytest.mark.asyncio
async def test_gemini_keeps_key_and_number_when_project_permission_missing(inventory):
    routes, _ = inventory
    routes["apikeys.googleapis.com", "/v2/keys:lookupKey?keyString=AIzaSYNTHETIC"] = {
        "name": "projects/123/locations/global/keys/key-id",
        "parent": "projects/123/locations/global",
    }
    routes["cloudresourcemanager.googleapis.com", "/v3/projects/123"] = (403, {})
    result = await resolver("gemini").resolve(discovery.KeyRequest("gemini", "AIzaSYNTHETIC"), {})
    assert result["ai.route.api_key_id"] == "key-id"
    assert result["ai.route.project_number"] == "123"
    assert result["ai.discovery.project_status"] == "permission_denied"
    assert "ai.route.project" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "info",
    [
        {"name": "", "parent": "projects/123/locations/global"},
        {"name": "projects/123/locations/global/keys/key-id", "parent": "projects/999/locations/global"},
        {"name": "https://evil.test/secret", "parent": "projects/123/locations/global"},
    ],
)
async def test_gemini_purged_or_invalid_keys_do_not_resolve(inventory, info):
    routes, _ = inventory
    routes["apikeys.googleapis.com", "/v2/keys:lookupKey?keyString=AIzaSYNTHETIC"] = info
    result = await resolver("gemini").resolve(discovery.KeyRequest("gemini", "AIzaSYNTHETIC"), {})
    assert result == {"ai.discovery.status": "invalid_response"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows,status",
    [
        ([], "not_found"),
        ([{"id": "apikey_a", "partial_key_hint": HINT}, {"id": "apikey_b", "partial_key_hint": HINT}], "ambiguous"),
        ([{"id": "sk-PRIVATE", "partial_key_hint": HINT}], "invalid_response"),
        ([{"id": "../secret", "partial_key_hint": HINT}], "invalid_response"),
        ([{"id": "apikey_a", "partial_key_hint": HINT, "workspace_id": "wrong-workspace"}], "not_found"),
    ],
)
async def test_no_unsafe_match(inventory, rows, status):
    routes, _ = inventory
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = page(*rows)
    result = await resolver().resolve(
        discovery.KeyRequest("anthropic", SECRET), {"ai.response.anthropic_workspace_id": "expected-workspace"}
    )
    assert result == {"ai.discovery.status": status}


@pytest.mark.asyncio
async def test_later_page_collision_is_not_accepted(inventory):
    routes, _ = inventory
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = page(
        {"id": "apikey_a", "partial_key_hint": HINT}, more=True, last="apikey_a"
    )
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100&after_id=apikey_a"] = page(
        {"id": "apikey_b", "partial_key_hint": HINT}
    )
    assert await resolver().resolve(discovery.KeyRequest("anthropic", SECRET), {}) == {
        "ai.discovery.status": "ambiguous"
    }


@pytest.mark.asyncio
async def test_admin_organization_must_match(inventory):
    routes, calls = inventory
    routes["api.anthropic.com", "/v1/organizations/me"] = {"id": "other-org"}
    assert await resolver().resolve(
        discovery.KeyRequest("anthropic", SECRET), {"ai.response.anthropic_organization_id": "expected"}
    ) == {"ai.discovery.status": "scope_mismatch"}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,status",
    [
        ((403, {}), "permission_denied"),
        ((401, {}), "permission_denied"),
        ((429, {}), "unavailable"),
        ((302, {}), "unavailable"),
        ([], "invalid_response"),
        ({"data": [], "has_more": True}, "invalid_response"),
        ({"data": [], "has_more": "false"}, "invalid_response"),
        ({"data": ["wrong shape"], "has_more": False}, "invalid_response"),
        ({"data": "a" * discovery._MAX_BYTES}, "inventory_limit"),
        (httpx.ReadTimeout(SECRET), "timeout"),
        (ValueError(SECRET), "unavailable"),
    ],
)
async def test_failed_lookups_are_safe_and_cached(inventory, body, status, caplog):
    routes, calls = inventory
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = body
    client = resolver()
    for _ in range(2):
        result = await client.resolve(discovery.KeyRequest("anthropic", SECRET), {})
        assert result == {"ai.discovery.status": status}
    assert len(calls) == 1
    assert SECRET not in caplog.text and ADMIN not in caplog.text


@pytest.mark.asyncio
async def test_total_timeout_and_concurrent_lookup_bound(inventory, monkeypatch):
    routes, calls = inventory
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def stall(request):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = stall
    monkeypatch.setattr(discovery, "_TIMEOUT", 0.05)
    client = resolver()
    request = discovery.KeyRequest("anthropic", SECRET)
    first = asyncio.create_task(client.resolve(request, {}))
    await entered.wait()
    assert await client.resolve(request, {}) == {"ai.discovery.status": "busy"}
    assert await first == {"ai.discovery.status": "timeout"}
    assert cancelled.is_set() and not client._inflight
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_cache_scope_rotation_expiry_fork_and_no_secrets(inventory, monkeypatch):
    routes, calls = inventory
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = page(
        {"id": "apikey_found", "partial_key_hint": HINT}
    )
    client = resolver()
    request = discovery.KeyRequest("anthropic", SECRET)
    result = await client.resolve(request, {})
    result["ai.route.api_key_id"] = "mutated"
    assert (await client.resolve(request, {}))["ai.route.api_key_id"] == "apikey_found"
    assert len(calls) == 1
    await client.resolve(request, {"ai.response.anthropic_workspace_id": "new-workspace"})
    monkeypatch.setenv("TEST_ADMIN_KEY", "sk-ROTATED-ADMIN")
    await client.resolve(request, {})
    await client.resolve(discovery.KeyRequest("anthropic", SECRET + "rotated"), {})
    client._cache = discovery.OrderedDict((key, (0, value)) for key, (_, value) in client._cache.items())
    await client.resolve(request, {})
    client._pid = -1
    await client.resolve(request, {})
    assert len(calls) == 6
    assert len(client._cache) == 1
    assert not any(s in repr(client._cache) for s in (SECRET, ADMIN, HINT, "ROTATED"))


@pytest.mark.asyncio
async def test_missing_credentials_does_not_make_requests(inventory, monkeypatch):
    _, calls = inventory
    monkeypatch.delenv("TEST_ADMIN_KEY")
    assert await resolver().resolve(discovery.KeyRequest("anthropic", SECRET), {}) == {
        "ai.discovery.status": "missing_credential"
    }
    assert not calls


@pytest.mark.asyncio
async def test_inventory_budget_does_not_return_partial_match(inventory, monkeypatch):
    routes, calls = inventory
    monkeypatch.setattr(discovery, "_MAX_REQUESTS", 1)
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = page(
        {"id": "apikey_candidate", "partial_key_hint": HINT}, more=True, last="apikey_candidate"
    )
    assert await resolver().resolve(discovery.KeyRequest("anthropic", SECRET), {}) == {
        "ai.discovery.status": "inventory_limit"
    }
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_repeated_pagination_cursor_fails_closed(inventory):
    routes, calls = inventory
    repeated = page({"id": "apikey_candidate", "partial_key_hint": HINT}, more=True, last="apikey_candidate")
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = repeated
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100&after_id=apikey_candidate"] = repeated
    assert await resolver().resolve(discovery.KeyRequest("anthropic", SECRET), {}) == {
        "ai.discovery.status": "invalid_response"
    }
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cache_and_concurrency_are_bounded(inventory):
    routes, calls = inventory
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = page()
    client = resolver()
    for i in range(257):
        await client.resolve(discovery.KeyRequest("anthropic", SECRET + str(i)), {})
    assert len(client._cache) == 256
    client._inflight = {str(i).encode() for i in range(4)}
    assert await client.resolve(discovery.KeyRequest("anthropic", "sk-new"), {}) == {"ai.discovery.status": "busy"}
    assert len(calls) == 257


@pytest.mark.asyncio
async def test_cancellation_closes_request_and_releases_slot(inventory):
    routes, _ = inventory
    entered = asyncio.Event()
    closed = asyncio.Event()

    async def stall(request):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            closed.set()

    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = stall
    client = resolver()
    task = asyncio.create_task(client.resolve(discovery.KeyRequest("anthropic", SECRET), {}))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set() and not client._inflight
    assert not client._cache


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,key,source", [(200, "apikey_discovered", "unique_key_hint"), (403, "apikey_manual", "configuration")]
)
async def test_callback_discovery_overrides_manual_only_on_success(inventory, status, key, source):
    routes, _ = inventory
    routes["api.anthropic.com", "/v1/organizations/api_keys?limit=100"] = (
        status,
        page({"id": "apikey_discovered", "partial_key_hint": HINT}),
    )
    records = []
    callback = make_callback(sink=records.append, provider_key_discovery={"anthropic": "TEST_ADMIN_KEY"})
    data = await start(callback)
    data.update(
        model="anthropic/claude-sonnet",
        custom_llm_provider="anthropic",
        api_base="https://api.anthropic.com",
        model_info={"id": "dep-1", "datadog_provider_api_key_id": "apikey_manual"},
    )
    await callback.async_pre_call_deployment_hook(data, "completion")
    callback.log_pre_api_call(None, None, {"litellm_params": data, "api_key": SECRET})
    assert SECRET not in repr(callback._pending)
    with mock.patch.object(httpx.AsyncClient, "send", side_effect=AssertionError("No instrumented HTTP client")):
        await finish(callback, data)
    assert records[0].tags["ai.route.api_key_id"] == key
    assert records[0].tags["ai.route.api_key_id_source"] == source
    assert not any(s in repr(records) for s in (SECRET, ADMIN, HINT))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cache_kwargs", [{"cache_hit": True}, {"cache_hit": None, "standard_logging_object": {"cache_hit": True}}]
)
async def test_fallback_clears_old_key_and_cache_hits_skip_discovery(inventory, cache_kwargs):
    _, calls = inventory
    records = []
    callback = make_callback(sink=records.append, provider_key_discovery={"anthropic": "TEST_ADMIN_KEY"})
    data = await start(callback)
    data.update(custom_llm_provider="anthropic", api_base="https://api.anthropic.com", model_info={"id": "dep-first"})
    await callback.async_pre_call_deployment_hook(data, "completion")
    callback.log_pre_api_call(None, None, {"litellm_params": data, "api_key": SECRET})
    data.update(custom_llm_provider="openai", api_base="https://api.openai.com", model_info={"id": "dep-1"})
    await callback.async_pre_call_deployment_hook(data, "completion")
    await finish(callback, data)
    assert "ai.route.api_key_id" not in records[0].tags
    assert not calls

    data = await start(callback)
    data.update(custom_llm_provider="anthropic", api_base="https://api.anthropic.com")
    callback.log_pre_api_call(None, None, {"litellm_params": data, "api_key": SECRET})
    await finish(callback, data, **cache_kwargs)
    assert not calls
