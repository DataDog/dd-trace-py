"""
Integration tests for the aio-pika (RabbitMQ) instrumentation.

Tests cover:
  - Exchange.publish creates a producer span with correct tags
  - Queue.consume callback (via consumer()) creates a consumer span with correct tags
  - Distributed tracing: trace context injected by producer, extracted by consumer
  - Disabled distributed tracing: producer and consumer spans are independent
  - Schematized operation names (v0 vs v1)
  - Schematized service names (default, DD_SERVICE, v0, v1)
  - Error propagation on failed publish
"""

import asyncio
from typing import Any
from typing import Optional

import aio_pika
from aio_pika import DeliveryMode
from aio_pika import ExchangeType
from aio_pika import Message
import aio_pika.exchange
import pytest

from ddtrace.contrib.internal.aio_pika.patch import patch
from ddtrace.contrib.internal.aio_pika.patch import unpatch
from ddtrace.ext import SpanTypes
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from tests.contrib.config import RABBITMQ_CONFIG
from tests.utils import TracerTestCase
from tests.utils import override_config


RABBITMQ_URL = "amqp://{user}:{password}@{host}:{port}/".format(**RABBITMQ_CONFIG)
EXCHANGE_NAME = "test_exchange_aio_pika"
QUEUE_NAME = "test_queue_aio_pika"
ROUTING_KEY = "test_routing_key_aio_pika"


# ---------------------------------------------------------------------------
# Async helpers
# ---------------------------------------------------------------------------


async def _connect() -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(RABBITMQ_URL)


async def _setup_channel(
    connection: aio_pika.abc.AbstractRobustConnection,
    exchange_name: str = EXCHANGE_NAME,
    queue_name: str = QUEUE_NAME,
    routing_key: str = ROUTING_KEY,
) -> tuple[Any, Any, Any]:
    """Declare exchange + queue and bind them. Returns (channel, exchange, queue)."""
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)

    exchange = await channel.declare_exchange(
        exchange_name,
        ExchangeType.DIRECT,
        durable=False,
        auto_delete=True,
    )
    queue = await channel.declare_queue(
        queue_name,
        durable=False,
        auto_delete=True,
        exclusive=True,
    )
    await queue.bind(exchange, routing_key=routing_key)
    return channel, exchange, queue


async def _publish_one(
    exchange: Any,
    routing_key: str = ROUTING_KEY,
    body: bytes = b"hello",
    headers: Optional[dict[str, Any]] = None,
) -> None:
    msg = Message(
        body=body,
        content_type="text/plain",
        delivery_mode=DeliveryMode.NOT_PERSISTENT,
        headers=headers or {},
    )
    await exchange.publish(msg, routing_key=routing_key)


async def _consume_one(queue: Any) -> aio_pika.abc.AbstractIncomingMessage:
    """Consume one message via Queue.consume callback pattern and return it."""
    received: list[aio_pika.abc.AbstractIncomingMessage] = []
    done = asyncio.Event()

    async def _on_msg(msg: aio_pika.IncomingMessage) -> None:
        received.append(msg)
        await msg.ack()
        done.set()

    consumer_tag = await queue.consume(_on_msg, no_ack=False)
    await asyncio.wait_for(done.wait(), timeout=10.0)
    await queue.cancel(consumer_tag)
    return received[0]


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def patch_aio_pika():
    """Patch aio-pika before every test and unpatch afterwards."""
    patch()
    yield
    unpatch()


@pytest.fixture
async def rabbitmq_connection():
    """Provide a connected aio-pika connection and close it after the test."""
    connection = await _connect()
    yield connection
    await connection.close()


@pytest.fixture
async def rabbitmq_setup(rabbitmq_connection):
    """Provide (connection, channel, exchange, queue) ready for use."""
    channel, exchange, queue = await _setup_channel(rabbitmq_connection)
    yield rabbitmq_connection, channel, exchange, queue


# ---------------------------------------------------------------------------
# Basic producer span tests
# ---------------------------------------------------------------------------


_SNAPSHOT_IGNORES = [
    "meta.runtime-id",
    "meta._dd.p.tid",
    "meta._dd.p.dm",
    "meta._dd.tags.process",
    "meta.tracestate",
    "metrics.process_id",
]


@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_publish_creates_producer_span(rabbitmq_setup, test_spans):
    """Exchange.publish should create a producer span with correct tags."""
    _conn, _ch, exchange, _q = rabbitmq_setup
    await _publish_one(exchange)


@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_consume_creates_consumer_span(rabbitmq_setup, test_spans):
    """Queue.consume callback should create a consumer span with correct tags."""
    _conn, _ch, exchange, queue = rabbitmq_setup
    await _publish_one(exchange)
    await _consume_one(queue)


# ---------------------------------------------------------------------------
# Tag assertions (non-snapshot)
# ---------------------------------------------------------------------------


async def test_producer_span_tags(rabbitmq_setup, test_spans):
    """Verify all expected tags on the producer span."""
    _conn, _ch, exchange, _q = rabbitmq_setup
    await _publish_one(exchange, body=b"test-body")

    spans = test_spans.pop()
    assert len(spans) >= 1, f"Expected at least 1 span, got {len(spans)}: {spans}"

    producer_spans = [s for s in spans if s.get_tag("span.kind") == "producer"]
    assert producer_spans, f"No producer span found among: {[s.name for s in spans]}"
    producer = producer_spans[0]

    assert producer.get_tag("component") == "aio_pika"
    assert producer.get_tag("span.kind") == "producer"
    assert producer.get_tag("messaging.system") == "rabbitmq"
    assert producer.span_type == SpanTypes.WORKER
    assert producer.error == 0


async def test_consumer_span_tags(rabbitmq_setup, test_spans):
    """Verify all expected tags on the consumer span."""
    _conn, _ch, exchange, queue = rabbitmq_setup
    await _publish_one(exchange, body=b"test-msg")
    await _consume_one(queue)

    spans = test_spans.pop()
    consumer_spans = [s for s in spans if s.get_tag("span.kind") == "consumer"]
    assert consumer_spans, f"No consumer span found among: {[s.name for s in spans]}"
    consumer = consumer_spans[0]

    assert consumer.get_tag("component") == "aio_pika"
    assert consumer.get_tag("span.kind") == "consumer"
    assert consumer.get_tag("messaging.system") == "rabbitmq"
    assert consumer.span_type == SpanTypes.WORKER
    assert consumer.error == 0


# ---------------------------------------------------------------------------
# Distributed tracing
# ---------------------------------------------------------------------------


async def test_distributed_tracing_enabled(rabbitmq_setup, test_spans):
    """With distributed tracing enabled, consumer trace_id should match producer trace_id."""
    _conn, _ch, exchange, queue = rabbitmq_setup

    with override_config("aio_pika", dict(distributed_tracing_enabled=True)):
        await _publish_one(exchange, body=b"dt-enabled")
        await _consume_one(queue)

    spans = test_spans.pop()
    producer_spans = [s for s in spans if s.get_tag("span.kind") == "producer"]
    consumer_spans = [s for s in spans if s.get_tag("span.kind") == "consumer"]

    assert producer_spans, "No producer span found"
    assert consumer_spans, "No consumer span found"

    assert producer_spans[0].trace_id == consumer_spans[0].trace_id, (
        f"trace_id mismatch: producer={producer_spans[0].trace_id} consumer={consumer_spans[0].trace_id}"
    )


async def test_distributed_tracing_disabled(rabbitmq_setup, test_spans):
    """With distributed tracing disabled, producer and consumer spans should be independent."""
    _conn, _ch, exchange, queue = rabbitmq_setup

    with override_config("aio_pika", dict(distributed_tracing_enabled=False)):
        await _publish_one(exchange, body=b"dt-disabled")
        await _consume_one(queue)

    spans = test_spans.pop()
    producer_spans = [s for s in spans if s.get_tag("span.kind") == "producer"]
    consumer_spans = [s for s in spans if s.get_tag("span.kind") == "consumer"]

    assert producer_spans, "No producer span found"
    assert consumer_spans, "No consumer span found"

    assert producer_spans[0].trace_id != consumer_spans[0].trace_id, (
        f"Expected independent trace IDs but both are {producer_spans[0].trace_id}"
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


async def test_publish_error_captured(rabbitmq_setup, test_spans):
    """If publish raises, the error should be captured on the producer span."""
    _conn, _ch, exchange, _q = rabbitmq_setup
    msg = Message(body=b"fail", delivery_mode=DeliveryMode.NOT_PERSISTENT)

    error_sentinel = RuntimeError("simulated publish error")

    # The instrumentation wraps Exchange.publish via wrapt.  To inject an error
    # while keeping the tracing wrapper intact we temporarily replace the
    # __wrapped__ attribute on the class-level FunctionWrapper (not the ephemeral
    # BoundFunctionWrapper), so the tracing code still fires but the underlying
    # call raises.
    cls_fw = aio_pika.exchange.Exchange.__dict__["publish"]

    async def _raising(*args: object, **kwargs: object) -> None:
        raise error_sentinel

    original_wrapped = cls_fw.__wrapped__
    cls_fw.__wrapped__ = _raising
    try:
        with pytest.raises(RuntimeError, match="simulated publish error"):
            await exchange.publish(msg, routing_key=ROUTING_KEY)
    finally:
        cls_fw.__wrapped__ = original_wrapped

    spans = test_spans.pop()
    error_spans = [s for s in spans if s.error]
    assert error_spans, f"Expected at least one error span, got: {[s.name for s in spans]}"


# ---------------------------------------------------------------------------
# Schematization
# ---------------------------------------------------------------------------


class TestAioPikaSchematization(TracerTestCase):
    """Test operation name and service name schematization via subprocess."""

    TEST_URL = RABBITMQ_URL

    def setUp(self):
        super().setUp()
        patch()

    def tearDown(self):
        unpatch()
        super().tearDown()

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0(self):
        async def _run():
            connection = await aio_pika.connect_robust(self.TEST_URL)
            channel, exchange, queue = await _setup_channel(connection)
            await _publish_one(exchange)
            await _consume_one(queue)
            await connection.close()

        asyncio.run(_run())
        spans = self.get_spans()
        producer_spans = [s for s in spans if s.get_tag("span.kind") == "producer"]
        consumer_spans = [s for s in spans if s.get_tag("span.kind") == "consumer"]
        assert producer_spans, f"No producer span. Spans: {[s.name for s in spans]}"
        assert consumer_spans, f"No consumer span. Spans: {[s.name for s in spans]}"
        assert producer_spans[0].name == "amqp.send", producer_spans[0].name
        assert consumer_spans[0].name == "amqp.receive", consumer_spans[0].name

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1(self):
        async def _run():
            connection = await aio_pika.connect_robust(self.TEST_URL)
            channel, exchange, queue = await _setup_channel(connection)
            await _publish_one(exchange)
            await _consume_one(queue)
            await connection.close()

        asyncio.run(_run())
        spans = self.get_spans()
        producer_spans = [s for s in spans if s.get_tag("span.kind") == "producer"]
        consumer_spans = [s for s in spans if s.get_tag("span.kind") == "consumer"]
        assert producer_spans, f"No producer span. Spans: {[s.name for s in spans]}"
        assert consumer_spans, f"No consumer span. Spans: {[s.name for s in spans]}"
        assert producer_spans[0].name == "rabbitmq.send", producer_spans[0].name
        assert consumer_spans[0].name == "rabbitmq.receive", consumer_spans[0].name

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_service_name_with_dd_service(self):
        async def _run():
            connection = await aio_pika.connect_robust(self.TEST_URL)
            channel, exchange, queue = await _setup_channel(connection)
            await _publish_one(exchange)
            await _consume_one(queue)
            await connection.close()

        asyncio.run(_run())
        spans = self.get_spans()
        assert spans, "Expected spans"
        for span in spans:
            assert span.service == "mysvc", f"Expected mysvc, got {span.service}"

    @TracerTestCase.run_in_subprocess(env_overrides=dict())
    def test_default_service_name(self):
        async def _run():
            connection = await aio_pika.connect_robust(self.TEST_URL)
            channel, exchange, queue = await _setup_channel(connection)
            await _publish_one(exchange)
            await connection.close()

        asyncio.run(_run())
        spans = self.get_spans()
        producer_spans = [s for s in spans if s.get_tag("span.kind") == "producer"]
        assert producer_spans, f"Expected producer span. Got: {[s.name for s in spans]}"
        assert producer_spans[0].service == "aio_pika", producer_spans[0].service

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_service_name_v1_no_dd_service(self):
        async def _run():
            connection = await aio_pika.connect_robust(self.TEST_URL)
            channel, exchange, queue = await _setup_channel(connection)
            await _publish_one(exchange)
            await connection.close()

        asyncio.run(_run())
        spans = self.get_spans()
        producer_spans = [s for s in spans if s.get_tag("span.kind") == "producer"]
        assert producer_spans, f"Expected producer span. Got: {[s.name for s in spans]}"
        assert producer_spans[0].service == DEFAULT_SPAN_SERVICE_NAME, producer_spans[0].service
