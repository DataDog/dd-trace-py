import asyncio

import aio_pika
import pytest

from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64

from .utils import aio_pika_ctx
from .utils import make_message
from .utils import queue_get_with_retry


_DSM_SNAPSHOT_IGNORES = [
    "meta.out.host",
    "metrics.network.destination.port",
    "meta.tracestate",
    "resource",
    "meta.messaging.destination.name",
]


@pytest.fixture
def dsm_processor():
    processor = data_streams_processor(reset=True)
    assert processor is not None, "Data Streams Monitoring is not enabled"
    yield processor
    # Clear buckets before shutdown to avoid long retry waits when no agent
    # is reachable (aio_pika suite only starts rabbitmq, not testagent).
    processor._buckets.clear()
    processor.shutdown(timeout=5)


@pytest.mark.asyncio
async def test_dsm_publish_sets_checkpoint(patch_aio_pika, dsm_processor, tracer, test_spans):
    """Publishing a message should create a DSM checkpoint with direction:out and type:rabbitmq."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("dsm publish test")
        await exchange.publish(msg, routing_key=routing_key)

    buckets = dsm_processor._buckets
    assert len(buckets) >= 1, "Expected at least one DSM bucket after publish"

    first = list(buckets.values())[0].pathway_stats
    found_produce = False
    for key in first.keys():
        edge_tags = key[0]
        if "direction:out" in edge_tags and "type:rabbitmq" in edge_tags:
            found_produce = True
            assert f"exchange:{exchange.name}" in edge_tags
            break
    assert found_produce, f"Expected produce checkpoint with direction:out, got keys: {list(first.keys())}"


@pytest.mark.asyncio
async def test_dsm_consume_sets_checkpoint(patch_aio_pika, dsm_processor, tracer, test_spans):
    """Consuming a message via Queue.consume should create a DSM checkpoint with direction:in."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("dsm consume test")
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

    buckets = dsm_processor._buckets
    assert len(buckets) >= 1, "Expected at least one DSM bucket after consume"

    first = list(buckets.values())[0].pathway_stats
    found_consume = False
    for key in first.keys():
        edge_tags = key[0]
        if "direction:in" in edge_tags and "type:rabbitmq" in edge_tags:
            found_consume = True
            break
    assert found_consume, f"Expected consume checkpoint with direction:in, got keys: {list(first.keys())}"


@pytest.mark.asyncio
async def test_dsm_get_sets_checkpoint(patch_aio_pika, dsm_processor, tracer, test_spans):
    """Consuming a message via Queue.get should create a DSM checkpoint with direction:in."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("dsm get test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        assert incoming.body == b"dsm get test"
        await incoming.ack()

    buckets = dsm_processor._buckets
    assert len(buckets) >= 1, "Expected at least one DSM bucket after get"

    first = list(buckets.values())[0].pathway_stats
    found_consume = False
    for key in first.keys():
        edge_tags = key[0]
        if "direction:in" in edge_tags and "type:rabbitmq" in edge_tags:
            found_consume = True
            assert f"topic:{queue.name}" in edge_tags
            break
    assert found_consume, f"Expected consume checkpoint with direction:in, got keys: {list(first.keys())}"


@pytest.mark.asyncio
async def test_dsm_pathway_propagation(patch_aio_pika, dsm_processor, tracer, test_spans):
    """DSM pathway context should be propagated from producer to consumer via message headers."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("dsm propagation test")
        await exchange.publish(msg, routing_key=routing_key)

        incoming = await queue_get_with_retry(queue, no_ack=False)
        assert incoming.headers is not None
        assert PROPAGATION_KEY_BASE_64 in incoming.headers, (
            f"Expected DSM pathway header '{PROPAGATION_KEY_BASE_64}' in message headers, "
            f"got headers: {incoming.headers}"
        )
        await incoming.ack()


@pytest.mark.asyncio
async def test_dsm_publish_consume_linked_pathway(patch_aio_pika, dsm_processor, tracer, test_spans):
    """Produce and consume checkpoints should be linked via parent hash in the pathway."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("dsm linked test")
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

    buckets = dsm_processor._buckets
    assert len(buckets) >= 1

    first = list(buckets.values())[0].pathway_stats
    produce_hash = None
    consume_parent_hash = None
    for key in first.keys():
        edge_tags, hash_val, parent_hash = key
        if "direction:out" in edge_tags:
            produce_hash = hash_val
            assert parent_hash == 0, "Produce checkpoint should have parent_hash=0"
        elif "direction:in" in edge_tags:
            consume_parent_hash = parent_hash

    assert produce_hash is not None, "Missing produce checkpoint"
    assert consume_parent_hash is not None, "Missing consume checkpoint"
    assert consume_parent_hash == produce_hash, (
        f"Consume parent hash ({consume_parent_hash}) should match produce hash ({produce_hash})"
    )


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=_DSM_SNAPSHOT_IGNORES)
async def test_dsm_span_has_pathway_hash(patch_aio_pika, dsm_processor):
    """Spans should have DSM pathway hash tag set."""
    async with aio_pika_ctx() as (channel, exchange, queue, routing_key):
        msg = make_message("dsm pathway hash test")
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
