import asyncio
from types import SimpleNamespace

import aio_pika
import pytest

from ddtrace.contrib.internal.aio_pika.patch import traced_get
from tests.utils import override_config

from .utils import aio_pika_ctx
from .utils import make_message
from .utils import queue_get_with_retry


_SNAPSHOT_IGNORES = [
    "meta.out.host",
    "metrics.network.destination.port",
    "meta.tracestate",
    # Queue/exchange names are randomised per test run for isolation.
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
async def test_publish_to_internal_exchange_raises(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        # AIDEV-NOTE: Newer pamqp versions reject internal exchanges while
        # declaring them, before Exchange.publish reaches the error path this
        # test verifies. An unchecked exchange reference exercises that path
        # consistently across the supported aio-pika versions.
        temp_exchange = await channel.get_exchange("internal_exchange_test", ensure=False)
        temp_exchange.internal = True

        msg = make_message("error test")
        with pytest.raises(ValueError, match="Can not publish to internal exchange"):
            await temp_exchange.publish(msg, routing_key="internal")


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_SNAPSHOT_IGNORES)
async def test_publish_sets_connection_tags_for_peer_service(patch_aio_pika):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("peer service test")
        await exchange.publish(msg, routing_key=routing_key)


@pytest.mark.asyncio
async def test_destination_name_uses_exchange_for_publish(patch_aio_pika, test_spans):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("resource name test")
        await exchange.publish(msg, routing_key=routing_key)

    spans = test_spans.pop()
    publish_span = next(s for s in spans if s.name == "rabbitmq.publish")
    assert publish_span.get_tag("messaging.destination.name") == exchange.name


@pytest.mark.asyncio
async def test_destination_name_uses_queue_for_get(patch_aio_pika, test_spans):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("resource name get test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        assert incoming.body == b"resource name get test"
        await incoming.ack()

    spans = test_spans.pop()
    get_span = next(s for s in spans if s.name == "rabbitmq.get")
    assert get_span.get_tag("messaging.destination.name") == queue.name


@pytest.mark.asyncio
async def test_resource_names_are_stable(patch_aio_pika, test_spans):
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        await exchange.publish(make_message("resource names"), routing_key=routing_key)
        incoming = await queue_get_with_retry(queue, no_ack=False)
        await incoming.ack()

    spans = test_spans.pop()
    resources = {span.name: span.resource for span in spans}
    assert resources["rabbitmq.publish"] == "rabbitmq.publish"
    assert resources["rabbitmq.get"] == "rabbitmq.get"
    assert resources["rabbitmq.ack"] == "rabbitmq.ack"


@pytest.mark.asyncio
async def test_queue_get_span_includes_wait_duration(patch_aio_pika, test_spans):
    async def slow_get(*args, **kwargs):
        await asyncio.sleep(0.05)
        return SimpleNamespace(headers={}, body=b"duration test")

    await traced_get(slow_get, SimpleNamespace(name="duration-queue"), (), {})

    spans = test_spans.pop()
    get_span = next(span for span in spans if span.name == "rabbitmq.get")
    assert get_span.duration >= 0.04


@pytest.mark.asyncio
@pytest.mark.parametrize("config_key", ["service", "service_name"])
async def test_service_override_applies_to_all_span_types(patch_aio_pika, test_spans, config_key):
    with override_config("aio_pika", {config_key: "custom-aio-pika"}):
        async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
            msg = make_message("service override test")
            await exchange.publish(msg, routing_key=routing_key)

            consume_done = asyncio.Event()

            async def on_message(message: aio_pika.abc.AbstractIncomingMessage):
                await message.ack()
                consume_done.set()

            consumer_tag = await queue.consume(on_message, no_ack=False)
            try:
                await asyncio.wait_for(consume_done.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pytest.fail("Timed out waiting for service override consumer callback")
            finally:
                await queue.cancel(consumer_tag)

    spans = test_spans.pop()
    assert len(spans) == 3
    assert {span.service for span in spans} == {"custom-aio-pika"}
