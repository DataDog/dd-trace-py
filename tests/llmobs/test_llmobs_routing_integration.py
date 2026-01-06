import asyncio
import json
from unittest import mock
from urllib.parse import urlparse

import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._routing import RoutingContext
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.utils import override_global_config


def _mock_span_event(name="test-span"):
    """Create a mock span event for testing."""
    return {
        "trace_id": "1234567890",
        "span_id": "0987654321",
        "parent_id": "undefined",
        "name": name,
        "start_ns": 1000000000,
        "duration": 1000000,
        "status": "ok",
        "meta": {"span": {"kind": "workflow"}},
        "metrics": {},
        "tags": ["ml_app:test-app"],
        "_dd": {"span_id": "0987654321", "trace_id": "1234567890"},
    }


class TestRoutingIntegration:
    """Integration tests for routing with LLMObs spans."""

    def test_span_in_routing_context_captures_routing(self, llmobs, llmobs_span_writer):
        """Spans created in routing_context have routing info captured."""
        with LLMObs.routing_context(dd_api_key="tenant-key-1", dd_site="tenant-site.com"):
            with LLMObs.workflow("test-workflow") as span:
                LLMObs.annotate(span, input_data="test input", output_data="test output")

        assert len(llmobs_span_writer.events) == 1
        routing_key = llmobs_span_writer._get_routing_key({"dd_api_key": "tenant-key-1", "dd_site": "tenant-site.com"})
        buffer = llmobs_span_writer._buffers.get(routing_key)
        assert buffer is not None
        assert len(buffer["events"]) == 1
        assert buffer["routing"]["dd_api_key"] == "tenant-key-1"
        assert buffer["routing"]["dd_site"] == "tenant-site.com"

    def test_spans_without_routing_context_use_default(self, llmobs, llmobs_span_writer):
        """Spans created without routing_context use default routing."""
        with LLMObs.workflow("test-workflow") as span:
            LLMObs.annotate(span, input_data="test input")

        assert len(llmobs_span_writer.events) == 1
        default_key = llmobs_span_writer._get_routing_key()
        buffer = llmobs_span_writer._buffers.get(default_key)
        assert buffer is not None
        assert len(buffer["events"]) == 1

    def test_nested_routing_contexts_override(self, llmobs, llmobs_span_writer):
        """Inner routing context overrides outer, restores after exit."""
        with LLMObs.routing_context(dd_api_key="outer-key", dd_site="outer.com"):
            with LLMObs.workflow("outer-span-before"):
                pass

            with LLMObs.routing_context(dd_api_key="inner-key", dd_site="inner.com"):
                with LLMObs.workflow("inner-span"):
                    pass

            with LLMObs.workflow("outer-span-after"):
                pass

        assert len(llmobs_span_writer.events) == 3

        outer_key = llmobs_span_writer._get_routing_key({"dd_api_key": "outer-key", "dd_site": "outer.com"})
        inner_key = llmobs_span_writer._get_routing_key({"dd_api_key": "inner-key", "dd_site": "inner.com"})

        outer_buffer = llmobs_span_writer._buffers.get(outer_key)
        inner_buffer = llmobs_span_writer._buffers.get(inner_key)

        assert outer_buffer is not None
        assert inner_buffer is not None
        assert len(outer_buffer["events"]) == 2
        assert len(inner_buffer["events"]) == 1

    def test_nested_spans_inherit_routing(self, llmobs, llmobs_span_writer):
        """Nested spans inherit routing from outer routing context."""
        with LLMObs.routing_context(dd_api_key="parent-key", dd_site="parent.com"):
            with LLMObs.workflow("parent-workflow"):
                with LLMObs.task("child-task"):
                    with LLMObs.llm(model_name="test-model", name="grandchild-llm"):
                        pass

        assert len(llmobs_span_writer.events) == 3

        routing_key = llmobs_span_writer._get_routing_key({"dd_api_key": "parent-key", "dd_site": "parent.com"})
        buffer = llmobs_span_writer._buffers.get(routing_key)
        assert buffer is not None
        assert len(buffer["events"]) == 3


class TestAsyncRouting:
    """Async integration tests."""

    @pytest.mark.asyncio
    async def test_concurrent_async_requests_isolate_data(self, llmobs, llmobs_span_writer):
        """Concurrent async requests with different routing do not leak data."""

        async def process_request(tenant_id, api_key, site):
            async with RoutingContext(dd_api_key=api_key, dd_site=site):
                with LLMObs.workflow(f"request-{tenant_id}") as span:
                    LLMObs.annotate(span, input_data=f"secret-data-{tenant_id}")
                    await asyncio.sleep(0.01)

        await asyncio.gather(
            process_request("tenant-1", "key-1", "site-1.com"),
            process_request("tenant-2", "key-2", "site-2.com"),
            process_request("tenant-3", "key-3", "site-3.com"),
        )

        assert len(llmobs_span_writer.events) == 3

        for i in range(1, 4):
            key = llmobs_span_writer._get_routing_key({"dd_api_key": f"key-{i}", "dd_site": f"site-{i}.com"})
            buffer = llmobs_span_writer._buffers.get(key)
            assert buffer is not None
            assert len(buffer["events"]) == 1
            event = buffer["events"][0]
            assert event["name"] == f"request-tenant-{i}"
            assert f"secret-data-tenant-{i}" in json.dumps(event["meta"])


class TestFlushBehavior:
    """Flush behavior tests."""

    def test_flush_sends_to_correct_endpoints(self, llmobs_span_writer):
        """Each buffer is flushed to its routing-specific endpoint."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            with mock.patch.object(writer, "_send_payload_for_routing_with_retry") as mock_send:
                event1 = _mock_span_event("span-1")
                event2 = _mock_span_event("span-2")

                writer.enqueue(event1, {"dd_api_key": "key-a", "dd_site": "site-a.com"})
                writer.enqueue(event2, {"dd_api_key": "key-b", "dd_site": "site-b.com"})

                writer.periodic()

                assert mock_send.call_count == 2

                calls = mock_send.call_args_list
                intakes = [call[0][2] for call in calls]
                headers_list = [call[0][3] for call in calls]

                hostnames = {urlparse(url).hostname for url in intakes}
                assert "llmobs-intake.site-a.com" in hostnames
                assert "llmobs-intake.site-b.com" in hostnames

                api_keys = [h["DD-API-KEY"] for h in headers_list]
                assert "key-a" in api_keys
                assert "key-b" in api_keys


class TestRoutingSecurity:
    """Security tests for multi-tenant routing."""

    def test_api_key_not_in_span_payload(self, llmobs, llmobs_span_writer):
        """API key should never appear in the span event payload."""
        with LLMObs.routing_context(dd_api_key="super-secret-key", dd_site="secret.com"):
            with LLMObs.workflow("test-workflow") as span:
                LLMObs.annotate(span, input_data="test", output_data="result")

        assert len(llmobs_span_writer.events) == 1
        event = llmobs_span_writer.events[0]

        event_json = json.dumps(event)
        assert "super-secret-key" not in event_json
        assert "DD-API-KEY" not in event_json

    def test_no_cross_tenant_data_in_events(self, llmobs, llmobs_span_writer):
        """Events for different tenants should not contain each other's data."""
        with LLMObs.routing_context(dd_api_key="tenant-a-key", dd_site="tenant-a.com"):
            with LLMObs.workflow("workflow-a") as span:
                LLMObs.annotate(span, input_data="secret-data-for-tenant-A")

        with LLMObs.routing_context(dd_api_key="tenant-b-key", dd_site="tenant-b.com"):
            with LLMObs.workflow("workflow-b") as span:
                LLMObs.annotate(span, input_data="secret-data-for-tenant-B")

        key_a = llmobs_span_writer._get_routing_key({"dd_api_key": "tenant-a-key", "dd_site": "tenant-a.com"})
        key_b = llmobs_span_writer._get_routing_key({"dd_api_key": "tenant-b-key", "dd_site": "tenant-b.com"})

        buffer_a = llmobs_span_writer._buffers.get(key_a)
        buffer_b = llmobs_span_writer._buffers.get(key_b)

        event_a_json = json.dumps(buffer_a["events"])
        event_b_json = json.dumps(buffer_b["events"])

        assert "secret-data-for-tenant-B" not in event_a_json
        assert "secret-data-for-tenant-A" not in event_b_json
        assert "tenant-b-key" not in event_a_json
        assert "tenant-a-key" not in event_b_json
