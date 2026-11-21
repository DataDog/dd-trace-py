import asyncio
import contextlib
from unittest import mock
import warnings

import aio_pika
import aiormq
import pytest

from ddtrace import config
from ddtrace.trace import tracer
from tests.contrib.aio_pika.conftest import AMQP_URL
from tests.utils import override_config


@pytest.fixture(autouse=True)
def _distributed_tracing_enabled():
    # Propagation is off by default (see test_aio_pika_config.py); these tests exercise it.
    with override_config("aio_pika", {"distributed_tracing_enabled": True}):
        yield


@pytest.mark.asyncio
async def test_publish_get_and_ack_with_rabbitmq(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    message = aio_pika.Message(b"payload", headers={"application": "preserved"}, message_id="message-1")

    confirmation = await channel.default_exchange.publish(message, routing_key=queue.name)
    incoming = await queue.get(timeout=2)
    await incoming.ack(multiple=False)

    assert confirmation is not None
    assert message.headers == {"application": "preserved"}
    assert incoming.headers["application"] == "preserved"
    assert "x-datadog-trace-id" in incoming.headers
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
    active_span_names = []

    async def callback(message):
        async with message.process():
            active_span_names.append(tracer.current_span().name)
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
    assert active_span_names == ["rabbitmq.consume"]
    assert actions[0].parent_id == processes[0].span_id


@pytest.mark.asyncio
async def test_process_context_is_active_during_cancellation(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    incoming = await queue.get(timeout=2)
    entered = asyncio.Event()

    async def process_message():
        async with incoming.process(requeue=False):
            assert tracer.current_span().name == "rabbitmq.consume"
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(process_message())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    process = next(span for span in test_spans.spans if span.name == "rabbitmq.consume")
    reject = next(span for span in test_spans.spans if span.name == "rabbitmq.reject")
    assert process.error == 1
    assert reject.parent_id == process.span_id


@pytest.mark.asyncio
async def test_publish_propagates_when_enabled(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    message = aio_pika.Message(b"payload", headers={"application": "preserved"})

    await channel.default_exchange.publish(message=message, routing_key=queue.name, mandatory=False)
    incoming = await queue.get(timeout=2)

    # Injection goes into a copy; the caller's message is not mutated.
    assert message.headers == {"application": "preserved"}
    assert incoming.headers["application"] == "preserved"
    assert "x-datadog-trace-id" in incoming.headers


@pytest.mark.asyncio
async def test_publish_propagation_can_be_disabled(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    message = aio_pika.Message(b"payload", headers={"application": "preserved"})

    with override_config("aio_pika", {"distributed_tracing_enabled": False}):
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

    # Newer aio-pika releases deprecate sync callbacks; 9.0.0 accepts them silently.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
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
    with override_config("aio_pika", {"distributed_tracing_enabled": False}):
        await channel.default_exchange.publish(message, routing_key=queue.name)

    with override_config("aio_pika", {"distributed_tracing_enabled": True}):
        with mock.patch.object(config, "_propagation_as_span_links", {"aio_pika"}):
            with tracer.trace("ambient") as ambient:
                incoming = await queue.get(timeout=2)
                await incoming.ack()

    receive = next(span for span in test_spans.spans if span.name == "rabbitmq.get")
    assert receive.parent_id == ambient.span_id
    assert [(link.trace_id, link.span_id) for link in receive._get_links()] == [(1234, 5678)]


@pytest.mark.asyncio
async def test_iterator_timeout_is_an_error_and_end_has_no_span(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    async with queue.iterator(timeout=0.05) as iterator:
        with pytest.raises(asyncio.TimeoutError):
            async for _ in iterator:
                pass

    receives = [span for span in test_spans.spans if span.name == "rabbitmq.get"]
    assert [span.error for span in receives] == [1]


@pytest.mark.asyncio
async def test_receive_does_not_leak_producer_context(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")

    with tracer.trace("local") as local:
        incoming = await queue.get(timeout=2)
        assert tracer.current_span() is local
    async with incoming.process():
        pass
    assert tracer.current_span() is None
    with tracer.trace("after") as after:
        pass

    receive = next(span for span in test_spans.spans if span.name == "rabbitmq.get")
    process = next(span for span in test_spans.spans if span.name == "rabbitmq.consume")
    assert receive.parent_id == publish.span_id
    assert process.parent_id == publish.span_id
    assert after.trace_id != publish.trace_id
    # Server-named (amq.gen-*) queue: the span name is the resource, not the per-consumer queue name.
    assert receive.resource == "rabbitmq.get"


@pytest.mark.asyncio
async def test_republish_incoming_message_does_not_mutate_it(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    source = await channel.declare_queue(exclusive=True, auto_delete=True)
    target = await channel.declare_queue(exclusive=True, auto_delete=True)
    with override_config("aio_pika", {"distributed_tracing_enabled": False}):
        await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=source.name)
    incoming = await source.get(timeout=2)
    headers_before = dict(incoming.headers)

    await channel.default_exchange.publish(incoming, routing_key=target.name)
    forwarded = await target.get(timeout=2)

    assert dict(incoming.headers) == headers_before
    assert forwarded.body == b"payload"
    assert "x-datadog-trace-id" in forwarded.headers


@pytest.mark.asyncio
async def test_settlement_outside_process_parents_on_producer(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")

    async with queue.iterator() as iterator:
        async for incoming in iterator:
            await incoming.nack(requeue=False)
            break
    assert tracer.current_span() is None

    nack = next(span for span in test_spans.spans if span.name == "rabbitmq.nack")
    assert nack.trace_id == publish.trace_id
    assert nack.parent_id == publish.span_id


@pytest.mark.asyncio
async def test_settlement_outside_process_links_producer_in_span_link_mode(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")

    with mock.patch.object(config, "_propagation_as_span_links", {"aio_pika"}):
        incoming = await queue.get(timeout=2)
        await incoming.ack()

    ack = next(span for span in test_spans.spans if span.name == "rabbitmq.ack")
    assert ack.trace_id != publish.trace_id
    assert [(link.trace_id, link.span_id) for link in ack._get_links()] == [(publish.trace_id, publish.span_id)]


@pytest.mark.asyncio
async def test_callable_object_callback(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    processed = asyncio.Event()

    class Handler:
        async def __call__(self, message):
            async with message.process():
                processed.set()

    consumer_tag = await queue.consume(Handler())
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    await asyncio.wait_for(processed.wait(), timeout=2)
    await queue.cancel(consumer_tag)
    await asyncio.sleep(0)

    processes = [span for span in test_spans.spans if span.name == "rabbitmq.consume"]
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")
    assert len(processes) == 1
    assert processes[0].parent_id == publish.span_id


@pytest.mark.asyncio
async def test_process_restores_caller_span(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")

    with tracer.trace("handler") as handler:
        incoming = await queue.get(timeout=2)
        async with incoming.process():
            assert tracer.current_span().name == "rabbitmq.consume"
        assert tracer.current_span() is handler

    process = next(span for span in test_spans.spans if span.name == "rabbitmq.consume")
    assert process.parent_id == publish.span_id


@pytest.mark.asyncio
async def test_closed_channel_does_not_break_processing(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    incoming = await queue.get(timeout=2)
    await channel.close()

    body_ran = False
    # aio-pika 10 itself raises from the settlement on exit once the channel is closed; the
    # instrumentation must not raise before the body runs.
    with contextlib.suppress(aiormq.exceptions.ChannelInvalidStateError):
        async with incoming.process():
            body_ran = True

    assert body_ran
    assert any(span.name == "rabbitmq.consume" for span in test_spans.spans)


@pytest.mark.asyncio
async def test_consumer_side_propagation_can_be_disabled(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")

    with override_config("aio_pika", {"distributed_tracing_enabled": False}):
        incoming = await queue.get(timeout=2)
        async with incoming.process():
            pass

    for name in ("rabbitmq.get", "rabbitmq.consume"):
        span = next(span for span in test_spans.spans if span.name == name)
        assert span.trace_id != publish.trace_id


@pytest.mark.asyncio
async def test_callback_span_link_mode(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)
    processed = asyncio.Event()

    async def callback(message):
        async with message.process():
            pass
        processed.set()

    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    publish = next(span for span in test_spans.spans if span.name == "rabbitmq.publish")
    with mock.patch.object(config, "_propagation_as_span_links", {"aio_pika"}):
        consumer_tag = await queue.consume(callback)
        await asyncio.wait_for(processed.wait(), timeout=2)
        await queue.cancel(consumer_tag)
        await asyncio.sleep(0)

    process = next(span for span in test_spans.spans if span.name == "rabbitmq.consume")
    assert process.trace_id != publish.trace_id
    assert [(link.trace_id, link.span_id) for link in process._get_links()] == [(publish.trace_id, publish.span_id)]


@pytest.mark.asyncio
async def test_robust_connection_consume_and_iterate(test_spans):
    connection = await aio_pika.connect_robust(AMQP_URL)
    try:
        channel = await connection.channel()
        # Not auto_delete: cancelling the only consumer would delete the queue before the second publish.
        queue = await channel.declare_queue(exclusive=True)
        processed = asyncio.Event()

        async def callback(message):
            async with message.process():
                processed.set()

        consumer_tag = await queue.consume(callback)
        await channel.default_exchange.publish(aio_pika.Message(b"push"), routing_key=queue.name)
        await asyncio.wait_for(processed.wait(), timeout=2)
        await queue.cancel(consumer_tag)

        await channel.default_exchange.publish(aio_pika.Message(b"pull"), routing_key=queue.name)
        async with queue.iterator() as iterator:
            async for incoming in iterator:
                await incoming.ack()
                break
    finally:
        await connection.close()

    publishes = [span for span in test_spans.spans if span.name == "rabbitmq.publish"]
    consume = next(span for span in test_spans.spans if span.name == "rabbitmq.consume")
    receives = [span for span in test_spans.spans if span.name == "rabbitmq.get"]
    assert consume.parent_id == publishes[0].span_id
    assert len(receives) == 1
    assert receives[0].parent_id == publishes[1].span_id
    assert receives[0].get_tag("out.host")


def test_server_named_queue_is_not_a_resource():
    from ddtrace.contrib.internal.aio_pika.patch import _resource

    assert _resource("orders") == "orders"
    assert _resource("amq.gen-JzTY20BRgKO-HjmUJj0wLg") is None
    assert _resource("amq.rabbitmq.reply-to") is None
    assert _resource("") is None


@pytest.mark.asyncio
async def test_cancelled_receive_wait_has_no_span(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    async def wait_for_message():
        async with queue.iterator() as iterator:
            async for _ in iterator:
                pass

    task = asyncio.create_task(wait_for_message())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not [span for span in test_spans.spans if span.name == "rabbitmq.get"]
