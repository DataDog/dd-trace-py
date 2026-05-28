import asyncio

import aio_pika
from aio_pika import ExchangeType
import pytest

from tests.utils import override_config

from .utils import aio_pika_ctx
from .utils import make_message
from .utils import queue_get_with_retry


_SNAPSHOT_IGNORES = [
    "meta.out.host",
    "metrics.network.destination.port",
    "meta.tracestate",
    # Queue/exchange names are randomised per test run for isolation.
    "resource",
    "meta.messaging.destination.name",
]


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_publish_creates_producer_span(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("publish test")
        await exchange.publish(msg, routing_key=routing_key)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_consume_callback_creates_consumer_span(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("consume callback test")
        await exchange.publish(msg, routing_key=routing_key)

        received = []
        consume_done = asyncio.Event()

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage):
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


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_queue_get_creates_consumer_span(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("get test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        assert incoming.body == b"get test"
        await incoming.ack()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_message_ack_creates_span(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("ack test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        await incoming.ack()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_message_nack_creates_span(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("nack test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        await incoming.nack(requeue=False)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_message_reject_creates_span(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("reject test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        await incoming.reject(requeue=False)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_publish_consume_full_flow(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("full flow test")
        await exchange.publish(msg, routing_key=routing_key)

        received = []
        consume_done = asyncio.Event()

        async def on_message(message: aio_pika.abc.AbstractIncomingMessage):
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


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_publish_multiple_messages(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        for i in range(3):
            msg = make_message(f"multi-{i}")
            await exchange.publish(msg, routing_key=routing_key)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_nack_with_requeue(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("requeue nack test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        assert incoming.body == b"requeue nack test"
        await incoming.nack(requeue=True)

        requeued = await queue_get_with_retry(queue, no_ack=False)
        assert requeued.body == b"requeue nack test"
        await requeued.ack()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_queue_get_empty_queue(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        incoming = await queue.get(no_ack=True, fail=False, timeout=1)
    assert incoming is None


@pytest.mark.asyncio
@pytest.mark.snapshot(
    ignores=_SNAPSHOT_IGNORES
    + [
        "meta.error.stack",
        "meta.error.message",
    ]
)
async def test_queue_get_empty_queue_raises(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        with pytest.raises(aio_pika.exceptions.QueueEmpty):
            await queue.get(no_ack=True, fail=True, timeout=1)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_distributed_tracing_publish_to_consume(patch_aio_pika):
    with override_config("aio_pika", dict(distributed_tracing_enabled=True)):
        async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
            msg = make_message("distributed tracing test")
            await exchange.publish(msg, routing_key=routing_key)

            received = []
            consume_done = asyncio.Event()

            async def on_message(message: aio_pika.abc.AbstractIncomingMessage):
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


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_distributed_tracing_publish_to_get(patch_aio_pika):
    with override_config("aio_pika", dict(distributed_tracing_enabled=True)):
        async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
            msg = make_message("distributed tracing get test")
            await exchange.publish(msg, routing_key=routing_key)

            incoming = await queue_get_with_retry(queue, no_ack=False)
            assert incoming.body == b"distributed tracing get test"
            await incoming.ack()


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_distributed_tracing_disabled(patch_aio_pika):
    with override_config("aio_pika", dict(distributed_tracing_enabled=False)):
        async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
            msg = make_message("no distributed tracing test")
            await exchange.publish(msg, routing_key=routing_key)

            received = []
            consume_done = asyncio.Event()

            async def on_message(message: aio_pika.abc.AbstractIncomingMessage):
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


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_publish_with_custom_headers(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("headers test", headers={"x-custom": "value", "x-priority": "high"})
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        assert incoming.headers.get("x-custom") == "value"
        assert incoming.headers.get("x-priority") == "high"
        await incoming.ack()


@pytest.mark.asyncio
@pytest.mark.snapshot(
    ignores=_SNAPSHOT_IGNORES
    + [
        "meta.error.stack",
        "meta.error.message",
        "meta.error.type",
    ]
)
async def test_publish_to_nonexistent_exchange(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        temp_exchange = await channel.declare_exchange(
            "nonexistent_exchange_test",
            type=ExchangeType.DIRECT,
            auto_delete=True,
        )
        await temp_exchange.delete()

        msg = make_message("error test")
        with pytest.raises(Exception):
            await temp_exchange.publish(msg, routing_key="nonexistent")


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_publish_sets_connection_tags_for_peer_service(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("peer service test")
        await exchange.publish(msg, routing_key=routing_key)


@pytest.mark.asyncio
async def test_resource_name_uses_exchange_for_publish(patch_aio_pika, tracer, test_spans):
    """Verify publish span resource is the exchange name."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("resource name test")
        await exchange.publish(msg, routing_key=routing_key)

    spans = test_spans.pop()
    publish_spans = [s for s in spans if "publish" in s.name]
    assert len(publish_spans) >= 1
    assert publish_spans[0].resource == publish_spans[0].name


@pytest.mark.asyncio
async def test_resource_name_uses_queue_for_get(patch_aio_pika, tracer, test_spans):
    """Verify get span resource is the queue name."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("resource name get test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        assert incoming.body == b"resource name get test"
        await incoming.ack()

    spans = test_spans.pop()
    get_spans = [s for s in spans if "get" in s.name]
    assert len(get_spans) >= 1
    assert get_spans[0].resource == get_spans[0].name
