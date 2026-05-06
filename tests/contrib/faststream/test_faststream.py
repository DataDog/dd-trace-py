"""Functional tests for the FastStream integration.

These exercise the integration against FastStream's in-memory test broker so
they require no Docker services. Both consumer and producer paths are covered,
along with distributed-trace propagation through message headers.
"""
import asyncio

import pytest

from ddtrace.contrib.internal.faststream._middleware import _DDTraceMiddleware
from ddtrace.contrib.internal.faststream._middleware import detect_messaging_system
from ddtrace.contrib.internal.faststream.patch import patch
from ddtrace.contrib.internal.faststream.patch import unpatch
from ddtrace.ext import SpanKind
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from tests.utils import TracerTestCase


@pytest.fixture(autouse=True)
def patched_faststream():
    patch()
    yield
    unpatch()


class TestFastStreamIntegration(TracerTestCase):
    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        unpatch()
        super().tearDown()

    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_middleware_attached_on_broker_construction(self):
        """Constructing any FastStream broker should auto-attach our middleware once."""
        from faststream.kafka import KafkaBroker

        broker = KafkaBroker("localhost:9092")

        middlewares = list(broker.config.broker_middlewares)
        assert any(isinstance(m, _DDTraceMiddleware) for m in middlewares)

        # Reconstructing should not double-register on the same config.
        broker2 = KafkaBroker("localhost:9092")
        assert sum(isinstance(m, _DDTraceMiddleware) for m in broker2.config.broker_middlewares) == 1

    def test_messaging_system_detection_kafka(self):
        from faststream.kafka import KafkaBroker

        broker = KafkaBroker("localhost:9092")
        assert detect_messaging_system(broker) == "kafka"

    def test_messaging_system_detection_unknown(self):
        class CustomBroker:
            pass

        assert detect_messaging_system(CustomBroker()) == "faststream"

    def test_consume_and_publish_spans(self):
        """End-to-end: in-memory test broker emits one consume + one publish span."""
        from faststream.kafka import KafkaBroker
        from faststream.kafka import TestKafkaBroker

        broker = KafkaBroker()

        @broker.subscriber("input-topic")
        @broker.publisher("output-topic")
        async def handler(msg: str) -> str:
            return f"processed: {msg}"

        async def run():
            async with TestKafkaBroker(broker) as test_broker:
                await test_broker.publish("hello", "input-topic")

        self._run(run())

        spans = self.get_spans()
        consume_spans = [s for s in spans if s.name.endswith("consume")]
        publish_spans = [s for s in spans if s.name.endswith("publish")]

        assert len(consume_spans) >= 1
        assert len(publish_spans) >= 1

        consume = consume_spans[0]
        assert consume.get_tag(COMPONENT) == "faststream"
        assert consume.get_tag(MESSAGING_SYSTEM) == "kafka"
        assert consume.get_tag(MESSAGING_DESTINATION_NAME) == "input-topic"
        assert consume.get_tag("span.kind") == SpanKind.CONSUMER
        # Schematized op-name is provider-specific.
        assert consume.name.startswith("kafka.")

        publish = publish_spans[-1]
        assert publish.get_tag(COMPONENT) == "faststream"
        assert publish.get_tag(MESSAGING_SYSTEM) == "kafka"
        assert publish.get_tag("span.kind") == SpanKind.PRODUCER

    def test_kafka_consume_tag_extraction(self):
        """Per-broker handler should populate Kafka-specific tags."""
        from types import SimpleNamespace

        from ddtrace.contrib.internal.faststream._middleware import _kafka_consume

        raw = SimpleNamespace(topic="t", partition=3, offset=42, key=b"k")
        msg = SimpleNamespace(raw_message=raw)
        destination, tags = _kafka_consume(msg)
        assert destination == "t"
        assert tags["messaging.kafka.partition"] == 3
        assert tags["messaging.kafka.message.offset"] == 42
        assert tags["messaging.kafka.message.key"] == b"k"

    def test_rabbit_consume_destination_format(self):
        """RabbitMQ destination is exchange.routing_key."""
        from types import SimpleNamespace

        from ddtrace.contrib.internal.faststream._middleware import _rabbit_consume

        raw = SimpleNamespace(exchange="ex", routing_key="rk", delivery_tag=7)
        msg = SimpleNamespace(raw_message=raw)
        destination, tags = _rabbit_consume(msg)
        assert destination == "ex.rk"
        assert tags["messaging.rabbitmq.destination.routing_key"] == "rk"
        assert tags["messaging.rabbitmq.message.delivery_tag"] == 7

    def test_publish_scope_injects_distributed_tracing_headers(self):
        """The publish_scope hook should mutate the outgoing command's headers
        to include the Datadog trace-context propagation keys."""
        from types import SimpleNamespace

        from faststream._internal.context.repository import ContextRepo

        from ddtrace.contrib.internal.faststream._middleware import _DDTraceMiddleware

        factory = _DDTraceMiddleware(messaging_system="kafka")
        middleware = factory(None, context=ContextRepo())

        cmd = SimpleNamespace(destination="t", headers={}, correlation_id="c-1")
        captured = {}

        async def call_next(c):
            captured["headers"] = dict(c.headers)
            return None

        self._run(middleware.publish_scope(call_next, cmd))

        header_keys_lower = {k.lower() for k in captured.get("headers", {}).keys()}
        # x-datadog-trace-id is emitted by the Datadog propagator regardless of
        # whether tracecontext / b3 / b3multi are also enabled.
        assert "x-datadog-trace-id" in header_keys_lower
