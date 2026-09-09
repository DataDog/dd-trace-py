import asyncio
from unittest import mock

import aio_pika
import pytest

from ddtrace import config
from ddtrace.trace import tracer
from tests.utils import override_config


@pytest.mark.asyncio
async def test_publish_get_and_ack_with_rabbitmq(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    message = aio_pika.Message(b"payload", headers={"application": "preserved"}, message_id="message-1")

    with override_config("aio_pika", {"distributed_tracing": True}):
        confirmation = await channel.default_exchange.publish(message, routing_key=queue.name)
        incoming = await queue.get(timeout=2)
        await incoming.ack(multiple=False)

    assert confirmation is not None
    assert message.headers["application"] == "preserved"
    assert "x-datadog-trace-id" in message.headers
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")
    receive = next(span for span in test_spans.spans if span.name == "rabbitmq.get")
    action = next(span for span in test_spans.spans if span.name == "rabbitmq.ack")
    assert receive.trace_id == publish.trace_id
    assert receive.parent_id == publish.span_id
    assert receive.get_tag("rabbitmq.queue") == queue.name
    assert action.get_tag("messaging.message_id") == "message-1"
    tags = repr([span.get_tags() for span in test_spans.spans])
    assert "payload" not in tags
    assert "preserved" not in tags
    assert "consumer_tag" not in tags
    assert "delivery_tag" not in tags


@pytest.mark.asyncio
async def test_callback_process_context_has_one_process_span(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    processed = asyncio.Event()

    async def callback(message):
        async with message.process():
            pass
        processed.set()

    consumer_tag = await queue.consume(callback)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    await asyncio.wait_for(processed.wait(), timeout=2)
    await queue.cancel(consumer_tag)
    await asyncio.sleep(0)

    processes = [span for span in test_spans.spans if span.name == "rabbitmq.consume"]
    actions = [span for span in test_spans.spans if span.name == "rabbitmq.ack"]
    assert len(processes) == 1
    assert len(actions) == 1
    assert actions[0].parent_id == processes[0].span_id


@pytest.mark.asyncio
async def test_publish_defaults_to_disabled_propagation(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    message = aio_pika.Message(b"payload", headers={"application": "preserved"})

    await channel.default_exchange.publish(message=message, routing_key=queue.name, mandatory=False)

    assert message.headers == {"application": "preserved"}
    span = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")
    assert span.get_tag("messaging.destination.name") == queue.name


@pytest.mark.asyncio
async def test_publish_failure_preserves_exception(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    exchange = aio_pika.Exchange(channel, "internal", internal=True)

    with pytest.raises(ValueError, match="Can not publish to internal exchange"):
        await exchange.publish(aio_pika.Message(b"payload"), routing_key="orders")

    span = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")
    assert span.error == 1


@pytest.mark.asyncio
async def test_get_empty_result_and_error(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    assert await queue.get(fail=False) is None
    with pytest.raises(aio_pika.exceptions.QueueEmpty):
        await queue.get(fail=True)

    spans = [span for span in test_spans.spans if span.name == "rabbitmq.get"]
    assert [span.error for span in spans] == [0, 1]
    assert all(span.get_tag("messaging.destination.name") == queue.name for span in spans)


@pytest.mark.asyncio
async def test_iterator_receive_covers_application_wait(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    async def delayed_publish():
        await asyncio.sleep(0.02)
        await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)

    publish_task = asyncio.create_task(delayed_publish())
    async with queue.iterator() as iterator:
        async for incoming in iterator:
            await incoming.ack()
            break
    await publish_task

    receive = next(span for span in test_spans.spans if span.name == "rabbitmq.get")
    assert receive.duration >= 0.02
    assert receive.get_tag("messaging.destination.name") == queue.name
    assert not [span for span in test_spans.spans if span.name == "rabbitmq.consume"]


@pytest.mark.asyncio
async def test_nack_reject_and_processed_errors(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    await channel.default_exchange.publish(aio_pika.Message(b"nack"), routing_key=queue.name)
    nacked = await queue.get(timeout=2)
    await nacked.nack(multiple=True, requeue=False)
    with pytest.raises(aio_pika.exceptions.MessageProcessError):
        await nacked.nack()

    await channel.default_exchange.publish(aio_pika.Message(b"reject"), routing_key=queue.name)
    rejected = await queue.get(timeout=2)
    await rejected.reject(requeue=False)

    actions = [span for span in test_spans.spans if span.name in {"rabbitmq.nack", "rabbitmq.reject"}]
    assert [span.name for span in actions] == ["rabbitmq.nack", "rabbitmq.nack", "rabbitmq.reject"]
    assert [span.error for span in actions] == [0, 1, 0]


@pytest.mark.asyncio
async def test_no_ack_settlement_error(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    incoming = await queue.get(no_ack=True, timeout=2)

    with pytest.raises(TypeError, match="no_ack"):
        await incoming.ack()

    action = next(span for span in test_spans.spans if span.name == "rabbitmq.ack")
    assert action.error == 1


@pytest.mark.asyncio
async def test_sync_callback(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    processed = asyncio.Event()

    def callback(message):
        processed.set()

    with pytest.warns(DeprecationWarning):
        consumer_tag = await queue.consume(callback, no_ack=True)
        await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
        await asyncio.wait_for(processed.wait(), timeout=2)
    await queue.cancel(consumer_tag)
    await asyncio.sleep(0)

    processes = [span for span in test_spans.spans if span.name == "rabbitmq.consume"]
    assert len(processes) == 1


@pytest.mark.asyncio
async def test_receive_span_link_uses_ambient_parent(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    message = aio_pika.Message(
        b"payload",
        headers={
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "5678",
        },
    )
    await channel.default_exchange.publish(message, routing_key=queue.name)

    with override_config("aio_pika", {"distributed_tracing": True}):
        with mock.patch.object(config, "_propagation_as_span_links", {"aio_pika"}):
            with tracer.trace("ambient") as ambient:
                incoming = await queue.get(timeout=2)
                await incoming.ack()

    receive = next(span for span in test_spans.spans if span.name == "rabbitmq.get")
    assert receive.parent_id == ambient.span_id
    assert [(link.trace_id, link.span_id) for link in receive._get_links()] == [(1234, 5678)]
