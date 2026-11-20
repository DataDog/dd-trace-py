import asyncio
import contextlib

import aio_pika
import pytest

from ddtrace.internal import core
import ddtrace.internal.datastreams as datastreams
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from tests.utils import override_global_config


@pytest.fixture
def dsm_processor():
    with override_global_config(dict(_data_streams_enabled=True)):
        # Registers the DSM listeners; they stay inert once DSM is disabled again.
        import ddtrace.internal.datastreams.aio_pika  # noqa: F401

        processor = data_streams_processor(reset=True)
        assert processor is not None
        try:
            yield processor
        finally:
            # Clear the buckets so shutdown does not wait on flushing them.
            processor._buckets.clear()
            processor.shutdown(timeout=5)
            datastreams._processor = None


@contextlib.contextmanager
def receive_events():
    events = []

    def record(queue, message, span):
        events.append(queue)

    core.on("aio_pika.receive.post", record)
    try:
        yield events
    finally:
        core.reset_listeners("aio_pika.receive.post", record)


def checkpoints(processor):
    """Return (edge_tags, hash, parent_hash) for every checkpoint recorded so far."""
    return [key for bucket in processor._buckets.values() for key in bucket.pathway_stats]


def checkpoint(processor, direction):
    matches = [key for key in checkpoints(processor) if f"direction:{direction}" in key[0]]
    assert len(matches) == 1, checkpoints(processor)
    return matches[0]


async def bound_queue(channel, routing_key):
    exchange = await channel.declare_exchange("dsm-orders", aio_pika.ExchangeType.TOPIC, auto_delete=True)
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await queue.bind(exchange, routing_key=routing_key)
    return exchange, queue


@pytest.mark.asyncio
async def test_named_exchange_checkpoints_link_through_headers(rabbitmq_connection, dsm_processor, test_spans):
    channel = await rabbitmq_connection.channel()
    exchange, queue = await bound_queue(channel, "order.created")
    message = aio_pika.Message(b"payload", headers={"application": "preserved"})

    await exchange.publish(message, routing_key="order.created")
    incoming = await queue.get(timeout=2)
    await incoming.ack()

    produce = checkpoint(dsm_processor, "out")
    consume = checkpoint(dsm_processor, "in")
    assert produce[0] == "direction:out,exchange:dsm-orders,has_routing_key:true,type:rabbitmq"
    assert consume[0] == f"direction:in,topic:{queue.name},type:rabbitmq"
    assert consume[2] == produce[1]
    # DSM propagates with trace propagation off, through a copy of the caller's message.
    assert PROPAGATION_KEY_BASE_64 in incoming.headers
    assert "x-datadog-trace-id" not in incoming.headers
    assert message.headers == {"application": "preserved"}
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")
    receive = next(span for span in test_spans.spans if span.name == "rabbitmq.get")
    assert publish.get_tag("pathway.hash") == str(produce[1])
    assert receive.get_tag("pathway.hash") == str(consume[1])


@pytest.mark.asyncio
async def test_default_exchange_uses_queue_as_topic(rabbitmq_connection, dsm_processor):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    incoming = await queue.get(timeout=2)
    await incoming.ack()

    produce = checkpoint(dsm_processor, "out")
    consume = checkpoint(dsm_processor, "in")
    # Both sides name the queue, as kombu does, so default-exchange producers link to their consumers.
    assert produce[0] == f"direction:out,topic:{queue.name},type:rabbitmq"
    assert consume[0] == f"direction:in,topic:{queue.name},type:rabbitmq"
    assert consume[2] == produce[1]


@pytest.mark.asyncio
async def test_callback_consume_is_tagged_with_queue_name(rabbitmq_connection, dsm_processor):
    channel = await rabbitmq_connection.channel()
    exchange, queue = await bound_queue(channel, "order.*")
    processed = asyncio.Event()

    async def callback(message):
        async with message.process():
            processed.set()

    consumer_tag = await queue.consume(callback)
    await exchange.publish(aio_pika.Message(b"payload"), routing_key="order.created")
    await asyncio.wait_for(processed.wait(), timeout=2)
    await queue.cancel(consumer_tag)

    produce = checkpoint(dsm_processor, "out")
    consume = checkpoint(dsm_processor, "in")
    # The topic is the queue, not the routing key the message was published with.
    assert consume[0] == f"direction:in,topic:{queue.name},type:rabbitmq"
    assert consume[2] == produce[1]


@pytest.mark.asyncio
async def test_pulled_message_is_checkpointed_once(rabbitmq_connection, dsm_processor):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    for body in (b"get", b"iterate"):
        await channel.default_exchange.publish(aio_pika.Message(body), routing_key=queue.name)

    with receive_events() as events:
        incoming = await queue.get(timeout=2)
        async with incoming.process():
            pass
        async with queue.iterator() as iterator:
            async for incoming in iterator:
                async with incoming.process():
                    pass
                break

    assert events == [queue.name, queue.name]


@pytest.mark.asyncio
async def test_disabled_dsm_adds_no_header(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    with receive_events() as events:
        await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
        incoming = await queue.get(timeout=2)
        await incoming.ack()

    assert PROPAGATION_KEY_BASE_64 not in (incoming.headers or {})
    assert events == []
    assert all(span.get_tag("pathway.hash") is None for span in test_spans.spans)
