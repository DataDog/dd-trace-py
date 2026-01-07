import pytest

from ddtrace.llmobs._routing import RoutingConfig
from ddtrace.llmobs._routing import RoutingContext
from ddtrace.llmobs._routing import _get_current_routing
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


class TestRoutingContextValidation:
    """Validation tests for RoutingContext."""

    def test_routing_context_requires_api_key(self):
        """Empty API key raises ValueError."""
        with pytest.raises(ValueError, match="dd_api_key is required"):
            RoutingContext(dd_api_key="")

    def test_routing_context_restores_on_exception(self):
        """Context is restored even when exception is raised."""
        try:
            with RoutingContext(dd_api_key="test-key"):
                raise ValueError("test error")
        except ValueError:
            pass

        assert _get_current_routing() is None


class TestMultiBufferWriter:
    """Unit tests for writer multi-buffer behavior."""

    def test_multiple_routing_keys_create_separate_buffers(self):
        """Different routing keys create separate buffers."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            routing_a: RoutingConfig = {"dd_api_key": "key-a", "dd_site": "site-a"}
            routing_b: RoutingConfig = {"dd_api_key": "key-b", "dd_site": "site-b"}

            writer.enqueue(_mock_span_event(), routing_a)
            writer.enqueue(_mock_span_event(), routing_b)
            writer.enqueue(_mock_span_event(), routing_a)

            assert len(writer._multi_tenant_buffers) == 2

            buffer_a = writer._multi_tenant_buffers.get("key-a:site-a")
            buffer_b = writer._multi_tenant_buffers.get("key-b:site-b")

            assert buffer_a is not None
            assert len(buffer_a["events"]) == 2
            assert buffer_b is not None
            assert len(buffer_b["events"]) == 1

    def test_get_headers_for_routing_sets_api_key(self):
        """Headers include the routing-specific API key."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            routing: RoutingConfig = {"dd_api_key": "tenant-key", "dd_site": "tenant-site"}
            headers = writer._get_headers_for_routing(routing)

            assert headers["DD-API-KEY"] == "tenant-key"

    def test_get_intake_for_routing_uses_custom_site(self):
        """Intake URL uses the routing-specific site."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="datadoghq.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            routing: RoutingConfig = {"dd_api_key": "tenant-key", "dd_site": "datadoghq.eu"}
            intake = writer._get_intake_for_routing(routing)

            assert "datadoghq.eu" in intake

    def test_buffer_limit_per_routing_key(self):
        """Each routing key buffer respects the buffer limit."""
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)
            writer.BUFFER_LIMIT = 2

            routing: RoutingConfig = {"dd_api_key": "tenant-key", "dd_site": "tenant-site"}

            writer.enqueue(_mock_span_event(), routing)
            writer.enqueue(_mock_span_event(), routing)
            writer.enqueue(_mock_span_event(), routing)

            buffer = writer._multi_tenant_buffers.get("tenant-key:tenant-site")
            assert len(buffer["events"]) == 2
