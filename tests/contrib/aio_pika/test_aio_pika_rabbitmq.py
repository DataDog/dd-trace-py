import asyncio

import aio_pika
import pytest

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
