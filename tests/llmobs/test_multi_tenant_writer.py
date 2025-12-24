import mock

from ddtrace.llmobs._routing import RoutingConfig
from ddtrace.llmobs._writer import LLMObsSpanWriter
from tests.utils import override_global_config


class TestMultiBufferWriter:
    def test_enqueue_default_routing(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)
            event = _mock_span_event()

            writer.enqueue(event)

            routing_key = writer._get_routing_key()
            buffer = writer._buffers.get(routing_key)
            assert buffer is not None
            assert len(buffer["events"]) == 1
            assert buffer["routing"]["dd_api_key"] == "default-key"

    def test_enqueue_with_routing(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)
            event = _mock_span_event()
            routing: RoutingConfig = {"dd_api_key": "tenant-key", "dd_site": "tenant-site"}

            writer.enqueue(event, routing)

            routing_key = writer._get_routing_key(routing)
            assert routing_key == "tenant-key:tenant-site"
            buffer = writer._buffers.get(routing_key)
            assert buffer is not None
            assert len(buffer["events"]) == 1
            assert buffer["routing"]["dd_api_key"] == "tenant-key"
            assert buffer["routing"]["dd_site"] == "tenant-site"

    def test_multiple_routing_keys_create_separate_buffers(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            event1 = _mock_span_event()
            event2 = _mock_span_event()
            event3 = _mock_span_event()

            routing_a: RoutingConfig = {"dd_api_key": "key-a", "dd_site": "site-a"}
            routing_b: RoutingConfig = {"dd_api_key": "key-b", "dd_site": "site-b"}

            writer.enqueue(event1, routing_a)
            writer.enqueue(event2, routing_b)
            writer.enqueue(event3, routing_a)

            assert len(writer._buffers) == 2

            buffer_a = writer._buffers.get("key-a:site-a")
            buffer_b = writer._buffers.get("key-b:site-b")

            assert buffer_a is not None
            assert len(buffer_a["events"]) == 2
            assert buffer_b is not None
            assert len(buffer_b["events"]) == 1

    def test_flush_clears_all_buffers(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            routing_a: RoutingConfig = {"dd_api_key": "key-a", "dd_site": "site-a"}
            routing_b: RoutingConfig = {"dd_api_key": "key-b", "dd_site": "site-b"}

            writer.enqueue(_mock_span_event(), routing_a)
            writer.enqueue(_mock_span_event(), routing_b)

            with mock.patch.object(writer, "_flush_buffer") as mock_flush:
                writer.periodic()
                assert mock_flush.call_count == 2

            assert len(writer._buffers) == 0

    def test_get_headers_for_routing_sets_api_key(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            routing: RoutingConfig = {"dd_api_key": "tenant-key", "dd_site": "tenant-site"}
            headers = writer._get_headers_for_routing(routing)

            assert headers["DD-API-KEY"] == "tenant-key"

    def test_get_headers_for_routing_uses_default(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            headers = writer._get_headers_for_routing(None)

            assert headers["DD-API-KEY"] == "default-key"

    def test_get_intake_for_routing_uses_custom_site(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="datadoghq.com")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            routing: RoutingConfig = {"dd_api_key": "tenant-key", "dd_site": "datadoghq.eu"}
            intake = writer._get_intake_for_routing(routing)

            assert "datadoghq.eu" in intake

    def test_buffer_limit_per_routing_key(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)
            writer.BUFFER_LIMIT = 2

            routing: RoutingConfig = {"dd_api_key": "tenant-key", "dd_site": "tenant-site"}

            writer.enqueue(_mock_span_event(), routing)
            writer.enqueue(_mock_span_event(), routing)
            writer.enqueue(_mock_span_event(), routing)

            buffer = writer._buffers.get("tenant-key:tenant-site")
            assert len(buffer["events"]) == 2

    def test_api_key_not_in_payload(self):
        with override_global_config(dict(_dd_api_key="default-key", _dd_site="default-site")):
            writer = LLMObsSpanWriter(interval=1.0, timeout=5.0, is_agentless=True)

            event = _mock_span_event()
            routing: RoutingConfig = {"dd_api_key": "secret-key", "dd_site": "tenant-site"}

            writer.enqueue(event, routing)

            buffer = writer._buffers.get("secret-key:tenant-site")
            payload = writer._data(buffer["events"])
            payload_str = str(payload)

            assert "secret-key" not in payload_str


def _mock_span_event():
    return {
        "span_id": "test-span-id",
        "trace_id": "test-trace-id",
        "parent_id": "undefined",
        "tags": ["env:test"],
        "name": "test-span",
        "start_ns": 1000000000,
        "duration": 100000000,
        "status": "ok",
        "meta": {"span": {"kind": "llm"}, "input": {}, "output": {}},
        "metrics": {},
        "_dd": {"ml_app": "test-app"},
    }
