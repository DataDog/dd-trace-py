"""
Tests for LLMObs routing context — multi-tenant routing and dual-shipping.

Use case 1 (multi-tenant): A platform routes LLMObs spans per-request to different customer orgs.
Use case 2 (dual-shipping): Internal teams send the same spans to multiple staging environments.
"""
import asyncio
import json
import os
import time

import mock
import pytest

from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import ROUTING_API_KEY
from ddtrace.llmobs._context import get_routing_context
from tests.utils import override_global_config


DD_SITE = "datad0g.com"
DD_API_KEY = os.getenv("DD_API_KEY", default="<not-a-real-api-key>")
TENANT_A_KEY = "tenant-a-api-key-1234"
TENANT_B_KEY = "tenant-b-api-key-5678"
TENANT_SITE = "us5.datadoghq.com"


# ===========================================================================
# routing_context() validation
# ===========================================================================


def test_routing_context_requires_api_key():
    with pytest.raises(ValueError, match="dd_api_key is required"):
        with llmobs_service.routing_context(dd_api_key=""):
            pass


def test_routing_context_requires_api_key_or_targets():
    with pytest.raises(ValueError):
        with llmobs_service.routing_context():
            pass


def test_routing_context_cannot_specify_both_api_key_and_targets():
    with pytest.raises(ValueError, match="Cannot specify both"):
        with llmobs_service.routing_context(
            dd_api_key=TENANT_A_KEY,
            targets=[{"dd_api_key": TENANT_B_KEY}],
        ):
            pass


def test_routing_context_targets_must_have_api_key():
    with pytest.raises(ValueError, match="dd_api_key.*required"):
        with llmobs_service.routing_context(targets=[{"dd_site": "foo.com"}]):
            pass


def test_routing_context_empty_targets_raises():
    with pytest.raises(ValueError):
        with llmobs_service.routing_context(targets=[]):
            pass


# ===========================================================================
# routing_context() lifecycle
# ===========================================================================


def test_routing_context_sets_and_clears():
    assert get_routing_context() is None
    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY, dd_site=TENANT_SITE):
        ctx = get_routing_context()
        assert ctx is not None
        assert ctx["targets"][0]["api_key"] == TENANT_A_KEY
        assert ctx["targets"][0]["site"] == TENANT_SITE
    assert get_routing_context() is None


def test_routing_context_site_is_optional():
    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
        ctx = get_routing_context()
        assert ctx["targets"][0]["api_key"] == TENANT_A_KEY
        assert "site" not in ctx["targets"][0]


def test_routing_context_single_target_normalized_to_targets_list():
    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY, dd_site=TENANT_SITE):
        ctx = get_routing_context()
        assert len(ctx["targets"]) == 1


def test_routing_context_nested_warns_and_restores(mock_llmobs_logs):
    with llmobs_service.routing_context(dd_api_key="outer-key"):
        with llmobs_service.routing_context(dd_api_key="inner-key-5678"):
            assert get_routing_context()["targets"][0]["api_key"] == "inner-key-5678"
            mock_llmobs_logs.warning.assert_called_once()
            assert "Nested routing_context" in mock_llmobs_logs.warning.call_args[0][0]
        assert get_routing_context()["targets"][0]["api_key"] == "outer-key"
    assert get_routing_context() is None


def test_routing_context_cleans_up_on_exception():
    with pytest.raises(RuntimeError):
        with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
            raise RuntimeError("boom")
    assert get_routing_context() is None


def test_routing_context_multiple_targets():
    targets = [
        {"dd_api_key": "key-ddstaging", "dd_site": "ddstaging.datadoghq.com"},
        {"dd_api_key": "key-datad0g", "dd_site": "datad0g.com"},
    ]
    with llmobs_service.routing_context(targets=targets):
        ctx = get_routing_context()
        assert len(ctx["targets"]) == 2
        assert ctx["targets"][0]["api_key"] == "key-ddstaging"
        assert ctx["targets"][1]["api_key"] == "key-datad0g"
    assert get_routing_context() is None


# ===========================================================================
# Span routing — single tenant
# ===========================================================================


def _wait_for_requests(reqs, num, attempts=1000):
    """Helper: poll until `num` requests have been captured by the test server."""
    for _ in range(attempts):
        if len(reqs) >= num:
            return reqs[:num]
        time.sleep(0.001)
    raise TimeoutError(f"Expected {num} requests, got {len(reqs)}")


def test_routed_span_sent_with_tenant_api_key(llmobs, _llmobs_backend):
    """A span created inside routing_context is sent with the tenant's API key."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY, dd_site=TENANT_SITE):
        with llmobs.workflow(name="routed-workflow"):
            llmobs.annotate(input_data="hello", output_data="world")

    # Wait for both the default flush and the routing flush
    _wait_for_requests(reqs, initial_count + 1)

    # Find the request with the tenant API key
    tenant_reqs = [r for r in reqs if r["headers"].get("Dd-Api-Key") == TENANT_A_KEY]
    assert len(tenant_reqs) >= 1, f"Expected request with tenant API key, got headers: {[r['headers'] for r in reqs]}"

    # Verify the payload contains our span
    body = json.loads(tenant_reqs[0]["body"])
    span_names = []
    for event in body if isinstance(body, list) else [body]:
        for s in event.get("spans", []):
            span_names.append(s.get("name"))
    assert "routed-workflow" in span_names


def test_unrouted_span_sent_with_default_api_key(llmobs, _llmobs_backend):
    """A span created outside routing_context is sent with the default API key."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs.workflow(name="default-workflow"):
        llmobs.annotate(input_data="hello")

    _wait_for_requests(reqs, initial_count + 1)

    # No request should have the tenant API key
    tenant_reqs = [r for r in reqs if r["headers"].get("Dd-Api-Key") == TENANT_A_KEY]
    assert len(tenant_reqs) == 0


def test_routed_and_unrouted_spans_go_to_different_keys(llmobs, _llmobs_backend):
    """Routed and unrouted spans in the same flush cycle have different API keys."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    # Create one routed span and one unrouted span
    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
        with llmobs.workflow(name="tenant-span"):
            llmobs.annotate(input_data="tenant")

    with llmobs.workflow(name="default-span"):
        llmobs.annotate(input_data="default")

    _wait_for_requests(reqs, initial_count + 2)

    api_keys_seen = set()
    for r in reqs[initial_count:]:
        key = r["headers"].get("Dd-Api-Key", "<default>")
        api_keys_seen.add(key)

    assert TENANT_A_KEY in api_keys_seen, f"Tenant API key not found in: {api_keys_seen}"


def test_child_span_inherits_routing(llmobs, _llmobs_backend):
    """Child spans inherit routing from parent — both end up at tenant."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
        with llmobs.workflow(name="parent"):
            with llmobs.llm(name="child", model_name="gpt-4") as child:
                llmobs.annotate(child, input_data="hello", output_data="world")

    _wait_for_requests(reqs, initial_count + 1)

    tenant_reqs = [r for r in reqs[initial_count:] if r["headers"].get("Dd-Api-Key") == TENANT_A_KEY]
    assert len(tenant_reqs) >= 1

    # Both parent and child should be in the tenant payload
    span_names = set()
    for r in tenant_reqs:
        body = json.loads(r["body"])
        for event in body if isinstance(body, list) else [body]:
            for s in event.get("spans", []):
                span_names.add(s.get("name"))
    assert "parent" in span_names
    assert "child" in span_names


def test_span_after_routing_context_is_unrouted(llmobs, _llmobs_backend):
    """Spans created after a routing context exits go to the default destination."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
        with llmobs.workflow(name="routed"):
            pass

    with llmobs.workflow(name="after"):
        llmobs.annotate(input_data="not routed")

    _wait_for_requests(reqs, initial_count + 2)

    # The "after" span should NOT have the tenant API key
    for r in reqs[initial_count:]:
        body = json.loads(r["body"])
        for event in body if isinstance(body, list) else [body]:
            for s in event.get("spans", []):
                if s.get("name") == "after":
                    assert r["headers"].get("Dd-Api-Key") != TENANT_A_KEY


# ===========================================================================
# Dual-shipping — same spans to multiple destinations
# ===========================================================================


def test_dual_ship_sends_to_both_destinations(llmobs, _llmobs_backend):
    """Dual-shipped spans appear in requests with both API keys."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    targets = [
        {"dd_api_key": "key-staging-a", "dd_site": "ddstaging.datadoghq.com"},
        {"dd_api_key": "key-staging-b", "dd_site": "datad0g.com"},
    ]
    with llmobs_service.routing_context(targets=targets):
        with llmobs.workflow(name="dual-shipped"):
            llmobs.annotate(input_data="hello")

    # Expect requests to both targets
    _wait_for_requests(reqs, initial_count + 2)

    api_keys = [r["headers"].get("Dd-Api-Key") for r in reqs[initial_count:]]
    assert "key-staging-a" in api_keys, f"key-staging-a not found in: {api_keys}"
    assert "key-staging-b" in api_keys, f"key-staging-b not found in: {api_keys}"


def test_dual_ship_both_payloads_contain_span(llmobs, _llmobs_backend):
    """Both destinations receive the same span data."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    targets = [
        {"dd_api_key": "key-staging-a"},
        {"dd_api_key": "key-staging-b"},
    ]
    with llmobs_service.routing_context(targets=targets):
        with llmobs.workflow(name="dual-shipped-span"):
            llmobs.annotate(input_data="payload")

    _wait_for_requests(reqs, initial_count + 2)

    for r in reqs[initial_count:]:
        body = json.loads(r["body"])
        span_names = []
        for event in body if isinstance(body, list) else [body]:
            for s in event.get("spans", []):
                span_names.append(s.get("name"))
        assert "dual-shipped-span" in span_names, f"Span not found in payload for key {r['headers'].get('Dd-Api-Key')}"


# ===========================================================================
# Multi-tenant isolation
# ===========================================================================


def test_concurrent_tenants_isolated(llmobs, _llmobs_backend):
    """Spans from different routing contexts don't mix."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
        with llmobs.workflow(name="tenant-a-span"):
            llmobs.annotate(input_data="a")

    with llmobs_service.routing_context(dd_api_key=TENANT_B_KEY):
        with llmobs.workflow(name="tenant-b-span"):
            llmobs.annotate(input_data="b")

    _wait_for_requests(reqs, initial_count + 2)

    for r in reqs[initial_count:]:
        body = json.loads(r["body"])
        span_names = set()
        for event in body if isinstance(body, list) else [body]:
            for s in event.get("spans", []):
                span_names.add(s.get("name"))
        key = r["headers"].get("Dd-Api-Key")
        if key == TENANT_A_KEY:
            assert "tenant-a-span" in span_names
            assert "tenant-b-span" not in span_names
        elif key == TENANT_B_KEY:
            assert "tenant-b-span" in span_names
            assert "tenant-a-span" not in span_names


def test_api_key_not_in_payload_body(llmobs, _llmobs_backend):
    """The tenant API key appears in headers only, never in the JSON payload body."""
    _, reqs = _llmobs_backend
    initial_count = len(reqs)

    with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
        with llmobs.workflow(name="secret-key-check"):
            llmobs.annotate(input_data="check")

    _wait_for_requests(reqs, initial_count + 1)

    tenant_reqs = [r for r in reqs[initial_count:] if r["headers"].get("Dd-Api-Key") == TENANT_A_KEY]
    for r in tenant_reqs:
        assert TENANT_A_KEY not in r["body"]


# ===========================================================================
# Async propagation
# ===========================================================================


def test_routing_context_propagates_to_async_tasks(llmobs):
    results = {}

    async def inner(key):
        results[key] = get_routing_context()

    async def main():
        with llmobs_service.routing_context(dd_api_key=TENANT_A_KEY):
            await inner("inside")
        await inner("outside")

    asyncio.run(main())
    assert results["inside"] is not None
    assert results["inside"]["targets"][0]["api_key"] == TENANT_A_KEY
    assert results["outside"] is None


def test_concurrent_async_routing_contexts_are_isolated(llmobs):
    results = {}

    async def tenant_work(api_key, result_key):
        with llmobs_service.routing_context(dd_api_key=api_key):
            await asyncio.sleep(0.01)
            results[result_key] = get_routing_context()

    async def main():
        await asyncio.gather(
            tenant_work(TENANT_A_KEY, "a"),
            tenant_work(TENANT_B_KEY, "b"),
        )

    asyncio.run(main())
    assert results["a"]["targets"][0]["api_key"] == TENANT_A_KEY
    assert results["b"]["targets"][0]["api_key"] == TENANT_B_KEY
