"""Functional tests for the FastStream integration.

Tests run against FastStream's in-memory ``Test*Broker`` fakes for every
supported broker, so no Docker services are required. The same middleware
auto-attach path is exercised across Kafka, Confluent, RabbitMQ, NATS, and
Redis.
"""
import asyncio
from types import SimpleNamespace

import pytest

from ddtrace.contrib.internal.faststream._middleware import _DDTraceMiddleware
from ddtrace.contrib.internal.faststream._middleware import _kafka_consume
from ddtrace.contrib.internal.faststream._middleware import _rabbit_consume
from ddtrace.contrib.internal.faststream._middleware import detect_messaging_system
from ddtrace.contrib.internal.faststream.patch import patch
from ddtrace.contrib.internal.faststream.patch import unpatch
from ddtrace.ext import SpanKind
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.constants import MESSAGING_DESTINATION_NAME
from ddtrace.internal.constants import MESSAGING_SYSTEM
from tests.utils import TracerTestCase


def _run(coro):
    """Run an async coroutine in tests; uses asyncio.run for 3.12+ safety."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _patched_faststream():
    patch()
    yield
    unpatch()


# AIDEV-NOTE: Per-broker matrix. Each entry is (import_path_for_broker_module,
# expected_messaging_system). The import-path module exposes a
# ``<X>Broker`` class and a ``Test<X>Broker`` test broker, both used below.
_BROKERS = [
    pytest.param(
        "faststream.kafka",
        "kafka",
        "KafkaBroker",
        "TestKafkaBroker",
        id="kafka",
    ),
    pytest.param(
        "faststream.confluent",
        "kafka",
        "KafkaBroker",
        "TestKafkaBroker",
        id="confluent",
    ),
    pytest.param(
        "faststream.rabbit",
        "rabbitmq",
        "RabbitBroker",
        "TestRabbitBroker",
        id="rabbit",
    ),
    pytest.param(
        "faststream.nats",
        "nats",
        "NatsBroker",
        "TestNatsBroker",
        id="nats",
    ),
    pytest.param(
        "faststream.redis",
        "redis",
        "RedisBroker",
        "TestRedisBroker",
        id="redis",
    ),
]


def _import_broker(module_path, broker_cls_name, test_broker_cls_name):
    """Import the broker classes; skip the test if the optional extra is not
    installed in the venv."""
    try:
        mod = __import__(module_path, fromlist=[broker_cls_name, test_broker_cls_name])
    except ImportError as e:
        pytest.skip(f"{module_path} extra not installed: {e}")
    return getattr(mod, broker_cls_name), getattr(mod, test_broker_cls_name)


class TestFastStreamUnit(TracerTestCase):
    """Pure unit tests for the per-broker dispatch helpers — no broker needed."""

    def test_messaging_system_detection_unknown(self):
        class CustomBroker:
            pass

        assert detect_messaging_system(CustomBroker()) == "faststream"

    def test_kafka_consume_tag_extraction(self):
        raw = SimpleNamespace(topic="t", partition=3, offset=42, key=b"k")
        msg = SimpleNamespace(raw_message=raw)
        destination, tags, links = _kafka_consume(msg)
        assert destination == "t"
        assert tags["messaging.kafka.partition"] == 3
        assert tags["messaging.kafka.message.offset"] == 42
        assert tags["messaging.kafka.message.key"] == b"k"
        assert links is None

    def test_rabbit_consume_destination_format(self):
        raw = SimpleNamespace(exchange="ex", routing_key="rk", delivery_tag=7)
        msg = SimpleNamespace(raw_message=raw)
        destination, tags, links = _rabbit_consume(msg)
        assert destination == "ex.rk"
        assert tags["messaging.rabbitmq.destination.routing_key"] == "rk"
        assert tags["messaging.rabbitmq.message.delivery_tag"] == 7
        assert links is None

    def test_publish_scope_injects_distributed_tracing_headers(self):
        from faststream._internal.context.repository import ContextRepo

        factory = _DDTraceMiddleware(messaging_system="kafka")
        middleware = factory(None, context=ContextRepo())

        cmd = SimpleNamespace(destination="t", headers={}, correlation_id="c-1")
        captured = {}

        async def call_next(c):
            captured["headers"] = dict(c.headers)
            return None

        _run(middleware.publish_scope(call_next, cmd))

        header_keys_lower = {k.lower() for k in captured.get("headers", {}).keys()}
        assert "x-datadog-trace-id" in header_keys_lower


class TestFastStreamBrokers(TracerTestCase):
    """End-to-end tests across every supported FastStream broker."""

    @pytest.fixture(autouse=True)
    def _capture_self(self):
        # Allow parametrized methods to access self.tracer / self.get_spans().
        yield

    @pytest.mark.parametrize("module_path,messaging_system,broker_cls,test_broker_cls", _BROKERS)
    def test_middleware_attached_on_broker_construction(
        self, module_path, messaging_system, broker_cls, test_broker_cls
    ):
        BrokerCls, _ = _import_broker(module_path, broker_cls, test_broker_cls)
        broker = BrokerCls()

        assert any(
            isinstance(m, _DDTraceMiddleware) for m in broker.config.broker_middlewares
        ), f"Datadog middleware not attached on {broker_cls}"

        # Constructing a second instance must not double-register.
        broker2 = BrokerCls()
        count = sum(isinstance(m, _DDTraceMiddleware) for m in broker2.config.broker_middlewares)
        assert count == 1, f"Middleware registered {count}x instead of once on {broker_cls}"

    @pytest.mark.parametrize("module_path,messaging_system,broker_cls,test_broker_cls", _BROKERS)
    def test_messaging_system_detection(
        self, module_path, messaging_system, broker_cls, test_broker_cls
    ):
        BrokerCls, _ = _import_broker(module_path, broker_cls, test_broker_cls)
        assert detect_messaging_system(BrokerCls()) == messaging_system

    @pytest.mark.parametrize("module_path,messaging_system,broker_cls,test_broker_cls", _BROKERS)
    def test_publish_consume_round_trip_emits_spans(
        self, module_path, messaging_system, broker_cls, test_broker_cls
    ):
        BrokerCls, TestBrokerCls = _import_broker(module_path, broker_cls, test_broker_cls)
        broker = BrokerCls()

        # Pick a destination keyword the broker accepts. RabbitMQ uses queue names;
        # NATS uses subjects; Redis uses channels; Kafka/Confluent use topics.
        # FastStream's @subscriber(...) accepts the first positional arg uniformly.
        @broker.subscriber("destination-x")
        async def handler(msg):
            return f"got: {msg}"

        async def run():
            async with TestBrokerCls(broker) as test_broker:
                await test_broker.publish("hello", "destination-x")

        _run(run())

        spans = self.get_spans()
        consume_spans = [s for s in spans if s.name.endswith("consume")]
        publish_spans = [s for s in spans if s.name.endswith("publish")]

        assert consume_spans, f"No consume span emitted for {messaging_system}"
        assert publish_spans, f"No publish span emitted for {messaging_system}"

        for s in consume_spans + publish_spans:
            assert s.get_tag(COMPONENT) == "faststream"
            assert s.get_tag(MESSAGING_SYSTEM) == messaging_system

        assert any(s.get_tag("span.kind") == SpanKind.CONSUMER for s in consume_spans)
        assert any(s.get_tag("span.kind") == SpanKind.PRODUCER for s in publish_spans)

    def test_kafka_publish_consume_distributed_trace_round_trip(self):
        """Critical: a publish span and the consume span it triggers must
        share the same trace_id when DT is enabled."""
        kafka = pytest.importorskip("faststream.kafka")
        BrokerCls = kafka.KafkaBroker
        TestBrokerCls = kafka.TestKafkaBroker

        broker = BrokerCls()

        @broker.subscriber("dt-topic")
        async def handler(msg):
            return msg

        async def run():
            async with TestBrokerCls(broker) as test_broker:
                await test_broker.publish("payload", "dt-topic")

        _run(run())

        spans = self.get_spans()
        publish_spans = [s for s in spans if s.name.endswith("publish")]
        consume_spans = [s for s in spans if s.name.endswith("consume")]
        assert publish_spans and consume_spans

        publish_trace = publish_spans[0].trace_id
        consume_trace = consume_spans[0].trace_id
        assert publish_trace == consume_trace, (
            f"Distributed trace not propagated: publish trace_id={publish_trace} "
            f"!= consume trace_id={consume_trace}"
        )

    def test_kafka_consume_span_has_bootstrap_servers_tag(self):
        """Kafka consume span should carry the bootstrap-servers tag."""
        kafka = pytest.importorskip("faststream.kafka")
        broker = kafka.KafkaBroker("broker-host:9092")

        @broker.subscriber("topic-bs")
        async def handler(msg):
            return msg

        async def run():
            async with kafka.TestKafkaBroker(broker) as tb:
                await tb.publish("x", "topic-bs")

        _run(run())

        spans = self.get_spans()
        consume_spans = [s for s in spans if s.name.endswith("consume")]
        assert consume_spans
        assert consume_spans[0].get_tag("messaging.kafka.bootstrap.servers") == "broker-host:9092"

    def test_kafka_publish_tombstone_tag(self):
        """Producer span must carry kafka.tombstone='True' when body is None."""
        kafka = pytest.importorskip("faststream.kafka")
        broker = kafka.KafkaBroker()

        @broker.subscriber("tomb-topic")
        async def handler(msg):
            return msg

        async def run():
            async with kafka.TestKafkaBroker(broker) as tb:
                await tb.publish(None, "tomb-topic")

        _run(run())

        spans = self.get_spans()
        publish_spans = [s for s in spans if s.name.endswith("publish")]
        assert publish_spans
        assert publish_spans[0].get_tag("kafka.tombstone") == "True"

    def test_kafka_consume_received_message_tag(self):
        """Consumer span must carry kafka.received_message='True'."""
        kafka = pytest.importorskip("faststream.kafka")
        broker = kafka.KafkaBroker()

        @broker.subscriber("recv-topic")
        async def handler(msg):
            return msg

        async def run():
            async with kafka.TestKafkaBroker(broker) as tb:
                await tb.publish("payload", "recv-topic")

        _run(run())

        spans = self.get_spans()
        consume_spans = [s for s in spans if s.name.endswith("consume")]
        assert consume_spans
        assert consume_spans[0].get_tag("kafka.received_message") == "True"

    def test_dsm_pathway_header_injected_when_enabled(self):
        """When DSM is enabled, the produce path injects ``dd-pathway-ctx``
        into the outgoing command's headers via the registered handler."""
        from ddtrace import config as ddconfig

        if not getattr(ddconfig, "_data_streams_enabled", False):
            pytest.skip("DSM not enabled in this test environment")

        # Lazily import so the DSM submodule is bound only if the env enables it
        kafka = pytest.importorskip("faststream.kafka")
        from faststream._internal.context.repository import ContextRepo

        from ddtrace.contrib.internal.faststream._middleware import _DDTraceMiddleware

        factory = _DDTraceMiddleware(messaging_system="kafka")
        middleware = factory(None, context=ContextRepo())

        cmd = SimpleNamespace(destination="dsm-topic", headers={}, body=b"x")
        captured = {}

        async def call_next(c):
            captured["headers"] = dict(c.headers)
            return None

        _run(middleware.publish_scope(call_next, cmd))

        header_keys_lower = {k.lower() for k in captured.get("headers", {}).keys()}
        assert any("pathway" in k for k in header_keys_lower), (
            f"DSM pathway header not injected; saw {header_keys_lower}"
        )

    def test_destination_tag_kafka(self):
        """Sanity: the consume span resource and destination tag are the topic."""
        kafka = pytest.importorskip("faststream.kafka")
        broker = kafka.KafkaBroker()

        @broker.subscriber("input-topic")
        async def handler(msg):
            return msg

        async def run():
            async with kafka.TestKafkaBroker(broker) as tb:
                await tb.publish("hello", "input-topic")

        _run(run())

        spans = self.get_spans()
        consume_spans = [s for s in spans if s.name.endswith("consume")]
        assert consume_spans[0].get_tag(MESSAGING_DESTINATION_NAME) == "input-topic"
        assert consume_spans[0].resource == "input-topic"
