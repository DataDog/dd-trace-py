"""
Multi-tenant routing tests for LLM Observability.

These tests mirror the Node.js multi-tenant.spec.js tests for consistency.
"""

import asyncio
import json
from unittest import mock

import pytest

from ddtrace.llmobs._routing import RoutingContext
from ddtrace.llmobs._routing import _get_current_routing
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.utils import override_global_config


class TestMultiTenantRouting:
    def test_routes_events_to_correct_endpoints_with_correct_api_keys(self):
        """Events are routed to correct endpoints with correct API keys."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            with mock.patch.object(writer, "_send_payload_for_routing_with_retry") as mock_send:
                writer.enqueue({"id": 1}, {"dd_api_key": "key-a", "dd_site": "site-a.com"})
                writer.enqueue({"id": 2}, {"dd_api_key": "key-b", "dd_site": "site-b.com"})
                writer.enqueue({"id": 3}, None)  # default routing

                writer.periodic()

                assert mock_send.call_count == 2

                calls = mock_send.call_args_list
                call_data = [{"api_key": call[0][3]["DD-API-KEY"], "intake": call[0][2]} for call in calls]

                api_keys = {c["api_key"] for c in call_data}
                assert "key-a" in api_keys
                assert "key-b" in api_keys

                intakes = {c["intake"] for c in call_data}
                assert any("site-a.com" in url for url in intakes)
                assert any("site-b.com" in url for url in intakes)

    def test_isolates_events_between_tenants(self):
        """Events for different tenants are isolated in separate buffers."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            with mock.patch.object(writer, "_send_payload_for_routing_with_retry") as mock_send:
                writer.enqueue({"tenant": "A", "secret": "A-data"}, {"dd_api_key": "key-a", "dd_site": "site-a.com"})
                writer.enqueue({"tenant": "B", "secret": "B-data"}, {"dd_api_key": "key-b", "dd_site": "site-b.com"})

                writer.periodic()

                payloads = []
                for call in mock_send.call_args_list:
                    api_key = call[0][3]["DD-API-KEY"]
                    events = json.loads(call[0][0])
                    payloads.append({"api_key": api_key, "events": events})

                payload_a = next(p for p in payloads if p["api_key"] == "key-a")
                payload_b = next(p for p in payloads if p["api_key"] == "key-b")

                assert len(payload_a["events"]) == 1
                assert payload_a["events"][0]["spans"][0]["secret"] == "A-data"
                assert len(payload_b["events"]) == 1
                assert payload_b["events"][0]["spans"][0]["secret"] == "B-data"

    def test_enforces_buffer_limit_per_routing_key(self):
        """Each routing key buffer respects the buffer limit."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)
            writer.BUFFER_LIMIT = 1000

            routing = {"dd_api_key": "key-a", "dd_site": "site-a.com"}
            for i in range(1001):
                writer.enqueue({"id": i}, routing)

            buffer = writer._multi_tenant_buffers.get("key-a")
            assert len(buffer["events"]) == 1000

    def test_clears_buffers_after_flush(self):
        """Buffers are cleared after flush."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            with mock.patch.object(writer, "_send_payload_for_routing_with_retry") as mock_send:
                writer.enqueue({"id": 1}, {"dd_api_key": "key-a", "dd_site": "site-a.com"})

                writer.periodic()
                assert mock_send.call_count == 1

                writer.periodic()
                assert mock_send.call_count == 1  # no new requests

    def test_api_key_not_in_payload_body(self):
        """API key should never appear in the payload body."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            with mock.patch.object(writer, "_send_payload_for_routing_with_retry") as mock_send:
                writer.enqueue({"data": "test"}, {"dd_api_key": "secret-tenant-key", "dd_site": "tenant.com"})

                writer.periodic()

                payload = mock_send.call_args_list[0][0][0]
                assert "secret-tenant-key" not in payload
                assert "default-key" not in payload

    def test_nested_contexts_override_and_restore(self):
        """Inner routing context overrides outer, restores after exit."""
        outer_routing = None
        inner_routing = None
        after_inner_routing = None

        with RoutingContext(dd_api_key="outer-key", dd_site="outer-site"):
            outer_routing = _get_current_routing()
            with RoutingContext(dd_api_key="inner-key", dd_site="inner-site"):
                inner_routing = _get_current_routing()
            after_inner_routing = _get_current_routing()

        assert outer_routing["dd_api_key"] == "outer-key"
        assert inner_routing["dd_api_key"] == "inner-key"
        assert after_inner_routing["dd_api_key"] == "outer-key"

    @pytest.mark.asyncio
    async def test_concurrent_contexts_are_isolated(self):
        """Concurrent async contexts are isolated from each other."""
        results = []

        async def task_a():
            async with RoutingContext(dd_api_key="key-a"):
                await asyncio.sleep(0.01)
                results.append({"context": "A", "routing": _get_current_routing()})

        async def task_b():
            async with RoutingContext(dd_api_key="key-b"):
                await asyncio.sleep(0.005)
                results.append({"context": "B", "routing": _get_current_routing()})

        await asyncio.gather(task_a(), task_b())

        result_a = next(r for r in results if r["context"] == "A")
        result_b = next(r for r in results if r["context"] == "B")

        assert result_a["routing"]["dd_api_key"] == "key-a"
        assert result_b["routing"]["dd_api_key"] == "key-b"
