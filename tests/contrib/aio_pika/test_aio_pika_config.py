import aio_pika
import pytest

from ddtrace import config


def test_distributed_tracing_is_off_by_default():
    assert config.aio_pika.distributed_tracing_enabled is False


@pytest.mark.asyncio
async def test_default_publish_does_not_propagate(rabbitmq_connection, test_spans):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue(exclusive=True, auto_delete=True)

    await channel.default_exchange.publish(aio_pika.Message(b"payload"), routing_key=queue.name)
    incoming = await queue.get(timeout=2)
    async with incoming.process():
        pass

    assert "x-datadog-trace-id" not in (incoming.headers or {})
    assert "traceparent" not in (incoming.headers or {})
    spans = {span.name: span for span in test_spans.spans}
    # Without propagation, the consumer side starts its own trace.
    assert spans["rabbitmq.consume"].trace_id != spans["rabbitmq.publish"].trace_id
    assert spans["rabbitmq.get"].parent_id is None
