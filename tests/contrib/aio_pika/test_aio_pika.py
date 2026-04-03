import asyncio

import aio_pika
from aio_pika import ExchangeType
import pytest

from tests.utils import override_config

from .utils import EXCHANGE_NAME
from .utils import QUEUE_NAME
from .utils import ROUTING_KEY
from .utils import aio_pika_ctx
from .utils import make_message


@pytest.mark.asyncio
async def test_publish_creates_producer_span(patch_aio_pika, tracer, test_spans):
    """Publishing a message to an exchange creates a producer span with correct tags."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("publish test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)

    traces = test_spans.pop_traces()
    assert len(traces) >= 1, "Expected at least one trace from publish"

    publish_span = traces[0][0]
    assert publish_span.name == "rabbitmq.publish"
    assert publish_span.span_type == "worker"
    assert publish_span.error == 0
    assert publish_span.resource == EXCHANGE_NAME
    assert publish_span.get_tag("span.kind") == "producer"
    assert publish_span.get_tag("messaging.system") == "rabbitmq"
    assert publish_span.get_tag("messaging.destination.name") == EXCHANGE_NAME
    assert publish_span.get_tag("messaging.operation") == "publish"
    assert publish_span.get_tag("component") == "aio-pika"


@pytest.mark.asyncio
async def test_consume_callback_creates_consumer_span(patch_aio_pika, tracer, test_spans):
    """Consuming a message via Queue.consume triggers a consumer span for each message."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("consume callback test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)

        received = []
        consume_done = asyncio.Event()

        async def on_message(message: aio_pika.IncomingMessage):
            received.append(message.body)
            await message.ack()
            consume_done.set()

        consumer_tag = await queue.consume(on_message, no_ack=False)
        try:
            await asyncio.wait_for(consume_done.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pytest.fail("Timed out waiting for consumer callback")
        finally:
            await queue.cancel(consumer_tag)

    assert len(received) == 1
    assert received[0] == b"consume callback test"

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]
    consumer_spans = [s for s in all_spans if s.name == "rabbitmq.receive"]
    assert len(consumer_spans) >= 1, "Expected at least one consumer span from callback consumption"

    consumer_span = consumer_spans[0]
    assert consumer_span.span_type == "worker"
    assert consumer_span.error == 0
    assert consumer_span.get_tag("span.kind") == "consumer"
    assert consumer_span.get_tag("messaging.system") == "rabbitmq"
    assert consumer_span.get_tag("messaging.operation") == "receive"
    assert consumer_span.get_tag("component") == "aio-pika"
    assert consumer_span.resource == EXCHANGE_NAME
    assert consumer_span.get_tag("messaging.destination.name") == EXCHANGE_NAME


@pytest.mark.asyncio
async def test_queue_get_creates_consumer_span(patch_aio_pika, tracer, test_spans):
    """Pulling a message via Queue.get creates a consumer span with correct tags."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("get test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)
        await asyncio.sleep(0.3)

        incoming = await queue.get(no_ack=False, fail=True, timeout=10)
        assert incoming.body == b"get test"
        await incoming.ack()

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    get_spans = [s for s in all_spans if s.name == "rabbitmq.get"]
    assert len(get_spans) >= 1, "Expected at least one span from Queue.get"

    get_span = get_spans[0]
    assert get_span.span_type == "worker"
    assert get_span.error == 0
    assert get_span.get_tag("span.kind") == "consumer"
    assert get_span.get_tag("messaging.system") == "rabbitmq"
    assert get_span.get_tag("messaging.operation") == "receive"
    assert get_span.get_tag("component") == "aio-pika"
    assert get_span.get_tag("messaging.destination.name") == QUEUE_NAME


@pytest.mark.asyncio
async def test_message_ack_creates_span(patch_aio_pika, tracer, test_spans):
    """Acknowledging a message via IncomingMessage.ack creates an internal span."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("ack test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)
        await asyncio.sleep(0.3)

        incoming = await queue.get(no_ack=False, fail=True, timeout=10)
        await incoming.ack()

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    ack_spans = [s for s in all_spans if s.name == "rabbitmq.ack"]
    assert len(ack_spans) >= 1, "Expected at least one ack span"

    ack_span = ack_spans[0]
    assert ack_span.span_type == "worker"
    assert ack_span.error == 0
    assert ack_span.resource == EXCHANGE_NAME
    assert ack_span.get_tag("span.kind") == "internal"
    assert ack_span.get_tag("messaging.system") == "rabbitmq"
    assert ack_span.get_tag("messaging.operation") == "ack"
    assert ack_span.get_tag("component") == "aio-pika"


@pytest.mark.asyncio
async def test_message_nack_creates_span(patch_aio_pika, tracer, test_spans):
    """Nacking a message via IncomingMessage.nack creates an internal span."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("nack test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)
        await asyncio.sleep(0.3)

        incoming = await queue.get(no_ack=False, fail=True, timeout=10)
        await incoming.nack(requeue=False)

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    nack_spans = [s for s in all_spans if s.name == "rabbitmq.nack"]
    assert len(nack_spans) >= 1, "Expected at least one nack span"

    nack_span = nack_spans[0]
    assert nack_span.span_type == "worker"
    assert nack_span.error == 0
    assert nack_span.resource == EXCHANGE_NAME
    assert nack_span.get_tag("span.kind") == "internal"
    assert nack_span.get_tag("messaging.system") == "rabbitmq"
    assert nack_span.get_tag("messaging.operation") == "nack"
    assert nack_span.get_tag("component") == "aio-pika"


@pytest.mark.asyncio
async def test_message_reject_creates_span(patch_aio_pika, tracer, test_spans):
    """Rejecting a message via IncomingMessage.reject creates an internal span."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("reject test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)
        await asyncio.sleep(0.3)

        incoming = await queue.get(no_ack=False, fail=True, timeout=10)
        await incoming.reject(requeue=False)

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    reject_spans = [s for s in all_spans if s.name == "rabbitmq.reject"]
    assert len(reject_spans) >= 1, "Expected at least one reject span"

    reject_span = reject_spans[0]
    assert reject_span.span_type == "worker"
    assert reject_span.error == 0
    assert reject_span.resource == EXCHANGE_NAME
    assert reject_span.get_tag("span.kind") == "internal"
    assert reject_span.get_tag("messaging.system") == "rabbitmq"
    assert reject_span.get_tag("messaging.operation") == "reject"
    assert reject_span.get_tag("component") == "aio-pika"


@pytest.mark.asyncio
async def test_publish_consume_full_flow(patch_aio_pika, tracer, test_spans):
    """Full publish-consume-ack flow creates correct producer and consumer spans."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("full flow test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)

        received = []
        consume_done = asyncio.Event()

        async def on_message(message: aio_pika.IncomingMessage):
            received.append(message.body)
            await message.ack()
            consume_done.set()

        consumer_tag = await queue.consume(on_message, no_ack=False)
        try:
            await asyncio.wait_for(consume_done.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pytest.fail("Timed out waiting for message in full flow test")
        finally:
            await queue.cancel(consumer_tag)

    assert received == [b"full flow test"]

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    consumer_spans = [s for s in all_spans if s.name == "rabbitmq.receive"]
    ack_spans = [s for s in all_spans if s.name == "rabbitmq.ack"]

    assert len(publish_spans) >= 1, "Expected at least one publish span"
    assert len(consumer_spans) >= 1, "Expected at least one consumer span"
    assert len(ack_spans) >= 1, "Expected at least one ack span"

    pub = publish_spans[0]
    assert pub.get_tag("span.kind") == "producer"
    assert pub.get_tag("messaging.system") == "rabbitmq"
    assert pub.get_tag("messaging.destination.name") == EXCHANGE_NAME
    assert pub.resource == EXCHANGE_NAME

    con = consumer_spans[0]
    assert con.get_tag("span.kind") == "consumer"
    assert con.get_tag("messaging.system") == "rabbitmq"
    assert con.get_tag("messaging.destination.name") == EXCHANGE_NAME
    assert con.get_tag("component") == "aio-pika"
    assert con.resource == EXCHANGE_NAME
    assert con.span_type == "worker"

    ack = ack_spans[0]
    assert ack.get_tag("span.kind") == "internal"
    assert ack.get_tag("messaging.system") == "rabbitmq"
    assert ack.get_tag("component") == "aio-pika"
    assert ack.resource == EXCHANGE_NAME
    assert ack.span_type == "worker"


@pytest.mark.asyncio
async def test_publish_multiple_messages(patch_aio_pika, tracer, test_spans):
    """Publishing multiple messages creates one producer span per message."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        for i in range(3):
            msg = make_message(f"multi-{i}")
            await exchange.publish(msg, routing_key=ROUTING_KEY)

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]
    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]

    assert len(publish_spans) == 3, f"Expected 3 publish spans, got {len(publish_spans)}"
    for span in publish_spans:
        assert span.get_tag("span.kind") == "producer"
        assert span.get_tag("messaging.system") == "rabbitmq"
        assert span.get_tag("messaging.destination.name") == EXCHANGE_NAME
        assert span.get_tag("component") == "aio-pika"
        assert span.resource == EXCHANGE_NAME
        assert span.error == 0


@pytest.mark.asyncio
async def test_nack_with_requeue(patch_aio_pika, tracer, test_spans):
    """Nacking a message with requeue=True creates a nack span and message is requeued."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("requeue nack test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)
        await asyncio.sleep(0.3)

        incoming = await queue.get(no_ack=False, fail=True, timeout=10)
        assert incoming.body == b"requeue nack test"
        await incoming.nack(requeue=True)

        await asyncio.sleep(0.3)
        requeued = await queue.get(no_ack=False, fail=True, timeout=10)
        assert requeued.body == b"requeue nack test"
        await requeued.ack()

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    nack_spans = [s for s in all_spans if s.name == "rabbitmq.nack"]
    assert len(nack_spans) >= 1, "Expected at least one nack span"
    assert nack_spans[0].get_tag("messaging.operation") == "nack"
    assert nack_spans[0].get_tag("messaging.system") == "rabbitmq"


@pytest.mark.asyncio
async def test_queue_get_empty_queue(patch_aio_pika, tracer, test_spans):
    """Getting from an empty queue with fail=False returns None without error."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        incoming = await queue.get(no_ack=True, fail=False, timeout=1)
    assert incoming is None

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    get_spans = [s for s in all_spans if s.name == "rabbitmq.get"]
    assert len(get_spans) == 1, "Expected exactly one get span for empty queue attempt"
    assert get_spans[0].error == 0
    assert get_spans[0].get_tag("messaging.system") == "rabbitmq"


@pytest.mark.asyncio
async def test_queue_get_empty_queue_raises(patch_aio_pika, tracer, test_spans):
    """Getting from an empty queue with fail=True raises QueueEmpty and sets error on span."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        with pytest.raises(aio_pika.exceptions.QueueEmpty):
            await queue.get(no_ack=True, fail=True, timeout=1)

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    get_spans = [s for s in all_spans if s.name == "rabbitmq.get"]
    assert len(get_spans) == 1, "Expected exactly one get span for failed empty queue attempt"
    error_span = get_spans[0]
    assert error_span.error == 1
    assert error_span.get_tag("error.type") is not None


@pytest.mark.asyncio
async def test_distributed_tracing_publish_to_consume(patch_aio_pika, tracer, test_spans):
    """When distributed tracing is enabled, consumer spans should be linked to producer spans."""
    with override_config("aio_pika", dict(distributed_tracing_enabled=True)):
        async with aio_pika_ctx() as (channel, exchange, queue):
            msg = make_message("distributed tracing test")
            await exchange.publish(msg, routing_key=ROUTING_KEY)

            received = []
            consume_done = asyncio.Event()

            async def on_message(message: aio_pika.IncomingMessage):
                received.append(message)
                await message.ack()
                consume_done.set()

            consumer_tag = await queue.consume(on_message, no_ack=False)
            try:
                await asyncio.wait_for(consume_done.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pytest.fail("Timed out waiting for distributed tracing consume")
            finally:
                await queue.cancel(consumer_tag)

    assert len(received) == 1

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    consumer_spans = [s for s in all_spans if s.name == "rabbitmq.receive"]

    assert len(publish_spans) >= 1
    assert len(consumer_spans) >= 1

    pub = publish_spans[0]
    con = consumer_spans[0]

    assert pub.trace_id == con.trace_id, f"Expected same trace_id: publish={pub.trace_id}, consume={con.trace_id}"
    assert con.parent_id == pub.span_id, (
        f"Expected consumer parent_id ({con.parent_id}) to match publisher span_id ({pub.span_id})"
    )


@pytest.mark.asyncio
async def test_distributed_tracing_publish_to_get(patch_aio_pika, tracer, test_spans):
    """When distributed tracing is enabled, Queue.get consumer spans should be linked to producer spans."""
    with override_config("aio_pika", dict(distributed_tracing_enabled=True)):
        async with aio_pika_ctx() as (channel, exchange, queue):
            msg = make_message("distributed tracing get test")
            await exchange.publish(msg, routing_key=ROUTING_KEY)
            await asyncio.sleep(0.3)

            incoming = await queue.get(no_ack=False, fail=True, timeout=10)
            assert incoming.body == b"distributed tracing get test"
            await incoming.ack()

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    get_spans = [s for s in all_spans if s.name == "rabbitmq.get"]

    assert len(publish_spans) >= 1
    assert len(get_spans) >= 1

    pub = publish_spans[0]
    get_span = get_spans[0]

    assert pub.trace_id == get_span.trace_id, (
        f"Expected same trace_id: publish={pub.trace_id}, get={get_span.trace_id}"
    )
    assert get_span.parent_id == pub.span_id, (
        f"Expected get parent_id ({get_span.parent_id}) to match publisher span_id ({pub.span_id})"
    )


@pytest.mark.asyncio
async def test_distributed_tracing_disabled(patch_aio_pika, tracer, test_spans):
    """When distributed tracing is disabled, consumer spans should NOT be linked to producer spans."""
    with override_config("aio_pika", dict(distributed_tracing_enabled=False)):
        async with aio_pika_ctx() as (channel, exchange, queue):
            msg = make_message("no distributed tracing test")
            await exchange.publish(msg, routing_key=ROUTING_KEY)

            received = []
            consume_done = asyncio.Event()

            async def on_message(message: aio_pika.IncomingMessage):
                received.append(message)
                await message.ack()
                consume_done.set()

            consumer_tag = await queue.consume(on_message, no_ack=False)
            try:
                await asyncio.wait_for(consume_done.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pytest.fail("Timed out waiting for disabled distributed tracing consume")
            finally:
                await queue.cancel(consumer_tag)

    assert len(received) == 1

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    consumer_spans = [s for s in all_spans if s.name == "rabbitmq.receive"]

    assert len(publish_spans) >= 1
    assert len(consumer_spans) >= 1

    pub = publish_spans[0]
    con = consumer_spans[0]

    assert pub.trace_id != con.trace_id, (
        f"Expected different trace_ids when distributed tracing is disabled: "
        f"publish={pub.trace_id}, consume={con.trace_id}"
    )


@pytest.mark.asyncio
async def test_service_name_override(patch_aio_pika, tracer, test_spans):
    """When a custom service name is configured, spans should use it."""
    with override_config("aio_pika", dict(service="my-custom-rabbitmq")):
        async with aio_pika_ctx() as (channel, exchange, queue):
            msg = make_message("service override test")
            await exchange.publish(msg, routing_key=ROUTING_KEY)

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    assert len(publish_spans) >= 1

    for span in publish_spans:
        assert span.service == "my-custom-rabbitmq", f"Expected service 'my-custom-rabbitmq', got '{span.service}'"


@pytest.mark.asyncio
async def test_publish_with_custom_headers(patch_aio_pika, tracer, test_spans):
    """Publishing a message with custom headers does not interfere with span creation."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("headers test", headers={"x-custom": "value", "x-priority": "high"})
        await exchange.publish(msg, routing_key=ROUTING_KEY)

        await asyncio.sleep(0.3)
        incoming = await queue.get(no_ack=False, fail=True, timeout=10)
        assert incoming.headers.get("x-custom") == "value"
        assert incoming.headers.get("x-priority") == "high"
        await incoming.ack()

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    assert len(publish_spans) >= 1
    pub = publish_spans[0]
    assert pub.get_tag("span.kind") == "producer"
    assert pub.get_tag("messaging.system") == "rabbitmq"
    assert pub.get_tag("messaging.destination.name") == EXCHANGE_NAME
    assert pub.get_tag("component") == "aio-pika"
    assert pub.resource == EXCHANGE_NAME
    assert pub.span_type == "worker"
    assert pub.error == 0


@pytest.mark.asyncio
async def test_publish_to_nonexistent_exchange(patch_aio_pika, tracer, test_spans):
    """Publishing to a non-existent exchange should create an error span."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        # Declare and immediately delete an exchange to ensure it doesn't exist
        temp_exchange = await channel.declare_exchange(
            "nonexistent_exchange_test",
            type=ExchangeType.DIRECT,
            auto_delete=True,
        )
        await temp_exchange.delete()

        msg = make_message("error test")
        with pytest.raises(Exception):
            await temp_exchange.publish(msg, routing_key="nonexistent")

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    assert len(publish_spans) == 1, "Expected exactly one publish span for nonexistent exchange"
    error_span = publish_spans[0]
    assert error_span.error == 1
    assert error_span.get_tag("error.type") is not None


@pytest.mark.asyncio
async def test_publish_sets_connection_tags_for_peer_service(patch_aio_pika, tracer, test_spans):
    """Publishing a message sets out.host and network.destination.port tags for peer service computation."""
    async with aio_pika_ctx() as (channel, exchange, queue):
        msg = make_message("peer service test")
        await exchange.publish(msg, routing_key=ROUTING_KEY)

    traces = test_spans.pop_traces()
    all_spans = [span for trace in traces for span in trace]

    publish_spans = [s for s in all_spans if s.name == "rabbitmq.publish"]
    assert len(publish_spans) >= 1, "Expected at least one publish span"

    pub = publish_spans[0]
    assert pub.get_tag("out.host") is not None, "Expected out.host tag on publish span"
    assert pub.get_tag("out.host") != "", "Expected non-empty out.host"
    assert pub.get_metric("network.destination.port") is not None, "Expected network.destination.port on publish span"
    assert pub.get_metric("network.destination.port") > 0, "Expected valid port number"

    assert pub.get_tag("span.kind") == "producer"
    assert pub.get_tag("messaging.system") == "rabbitmq"
    assert pub.get_tag("messaging.destination.name") == EXCHANGE_NAME
    assert pub.get_tag("component") == "aio-pika"
    assert pub.resource == EXCHANGE_NAME
