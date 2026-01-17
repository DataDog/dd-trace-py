import asyncio
import json
import logging
from unittest import mock

import pytest

from ddtrace.llmobs._routing import RoutingContext
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.utils import override_global_config


class TestMultiTenantWriterLowLevel:
    """Low-level tests for multi-tenant writer buffering (without full SDK)."""

    def test_routes_events_to_correct_endpoints_with_correct_api_keys(self):
        """Events are routed to correct endpoints with correct API keys."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            with mock.patch.object(writer, "_send_payload_with_retry") as mock_send:
                writer.enqueue({"id": 1}, {"dd_api_key": "key-a", "dd_site": "site-a.com"})
                writer.enqueue({"id": 2}, {"dd_api_key": "key-b", "dd_site": "site-b.com"})
                writer.enqueue({"id": 3}, None)  # default routing

                writer.periodic()

                # 3 calls: 2 custom routing + 1 default
                assert mock_send.call_count == 3

                # Filter to only custom routing calls (those with intake and headers args)
                custom_calls = [c for c in mock_send.call_args_list if len(c[0]) == 4]
                assert len(custom_calls) == 2

                call_data = [{"api_key": call[0][3]["DD-API-KEY"], "intake": call[0][2]} for call in custom_calls]

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

            with mock.patch.object(writer, "_send_payload_with_retry") as mock_send:
                writer.enqueue({"tenant": "A", "secret": "A-data"}, {"dd_api_key": "key-a", "dd_site": "site-a.com"})
                writer.enqueue({"tenant": "B", "secret": "B-data"}, {"dd_api_key": "key-b", "dd_site": "site-b.com"})

                writer.periodic()

                custom_calls = [c for c in mock_send.call_args_list if len(c[0]) == 4]
                payloads = []
                for call in custom_calls:
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

            with mock.patch.object(writer, "_send_payload_with_retry") as mock_send:
                writer.enqueue({"id": 1}, {"dd_api_key": "key-a", "dd_site": "site-a.com"})

                writer.periodic()
                first_count = mock_send.call_count
                assert first_count >= 1

                writer.periodic()
                assert mock_send.call_count == first_count  # no new requests

    def test_api_key_not_in_payload_body(self):
        """API key should never appear in the payload body."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            with mock.patch.object(writer, "_send_payload_with_retry") as mock_send:
                writer.enqueue({"data": "test"}, {"dd_api_key": "secret-tenant-key", "dd_site": "tenant.com"})

                writer.periodic()

                custom_calls = [c for c in mock_send.call_args_list if len(c[0]) == 4]
                assert len(custom_calls) == 1
                payload = custom_calls[0][0][0]
                assert "secret-tenant-key" not in payload
                assert "default-key" not in payload


class TestMultiTenantRoutingContext:
    """Tests for routing context behavior using the real SDK."""

    def test_nested_contexts_route_spans_correctly_and_log_warning(self, llmobs, llmobs_span_writer, caplog):
        """Nested routing contexts route spans correctly and log warning."""
        with mock.patch.object(llmobs_span_writer, "enqueue", wraps=llmobs_span_writer.enqueue) as enqueue_spy:
            with caplog.at_level(logging.WARNING, logger="ddtrace.llmobs._routing"):
                with RoutingContext(dd_api_key="outer-key", dd_site="outer-site.com"):
                    with llmobs.workflow(name="outer-span"):
                        pass

                    with RoutingContext(dd_api_key="inner-key", dd_site="inner-site.com"):
                        with llmobs.workflow(name="inner-span"):
                            pass

                    with llmobs.workflow(name="after-inner-span"):
                        pass

            calls = enqueue_spy.call_args_list
            assert len(calls) == 3

            def routing_for(name):
                return next(c[0][1] for c in calls if c[0][0]["name"] == name)

            assert routing_for("outer-span") == {"dd_api_key": "outer-key", "dd_site": "outer-site.com"}
            assert routing_for("inner-span") == {"dd_api_key": "inner-key", "dd_site": "inner-site.com"}
            assert routing_for("after-inner-span") == {"dd_api_key": "outer-key", "dd_site": "outer-site.com"}

            assert "Nested routing context detected" in caplog.text

    @pytest.mark.asyncio
    async def test_concurrent_contexts_are_isolated(self, llmobs, llmobs_span_writer):
        """Concurrent async contexts are isolated from each other."""
        with mock.patch.object(llmobs_span_writer, "enqueue", wraps=llmobs_span_writer.enqueue) as enqueue_spy:

            async def task_a():
                async with RoutingContext(dd_api_key="key-a", dd_site="site-a.com"):
                    await asyncio.sleep(0.01)
                    with llmobs.workflow(name="span-a"):
                        pass

            async def task_b():
                async with RoutingContext(dd_api_key="key-b", dd_site="site-b.com"):
                    await asyncio.sleep(0.005)
                    with llmobs.workflow(name="span-b"):
                        pass

            await asyncio.gather(task_a(), task_b())

            calls = enqueue_spy.call_args_list
            routing_a = next(c[0][1] for c in calls if c[0][0]["name"] == "span-a")
            routing_b = next(c[0][1] for c in calls if c[0][0]["name"] == "span-b")

            assert routing_a["dd_api_key"] == "key-a"
            assert routing_b["dd_api_key"] == "key-b"
