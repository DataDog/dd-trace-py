import asyncio

import aio_pika
import pytest

from tests.utils import override_config


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.tracestate"])
async def test_publish_get_process_and_ack(rabbitmq_connection):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue("ddtrace-aio-pika-snapshot", durable=True)
    message = aio_pika.Message(
        b"payload",
        headers={"application": "preserved"},
        message_id="snapshot-message",
    )

    try:
        with override_config("aio_pika", {"distributed_tracing": True}):
            confirmation = await channel.default_exchange.publish(message, routing_key=queue.name)
            incoming = await queue.get(timeout=2)
            assert incoming is not None
            async with incoming.process():
                pass

        assert confirmation is not None
        assert message.headers["application"] == "preserved"
        assert "x-datadog-trace-id" in message.headers
    finally:
        await queue.delete(if_unused=False, if_empty=False)


@pytest.mark.asyncio
@pytest.mark.snapshot(ignores=["meta.tracestate"])
async def test_publish_callback_process_and_ack(rabbitmq_connection):
    channel = await rabbitmq_connection.channel()
    queue = await channel.declare_queue("ddtrace-aio-pika-callback-snapshot", durable=True)
    processed = asyncio.Event()

    async def callback(message):
        async with message.process():
            pass
        processed.set()

    try:
        with override_config("aio_pika", {"distributed_tracing": True}):
            consumer_tag = await queue.consume(callback)
            await channel.default_exchange.publish(
                aio_pika.Message(b"payload", message_id="callback-snapshot-message"),
                routing_key=queue.name,
            )
            await asyncio.wait_for(processed.wait(), timeout=2)
            await queue.cancel(consumer_tag)
            await asyncio.sleep(0)
    finally:
        await queue.delete(if_unused=False, if_empty=False)


@pytest.mark.snapshot(ignores=["meta.tracestate"])
@pytest.mark.subprocess(
    env={
        "DD_AIO_PIKA_DISTRIBUTED_TRACING": "true",
        "DD_AIO_PIKA_SERVICE": "aio-pika-service",
        "DD_SERVICE": "application-service",
        "DD_TRACE_SPAN_ATTRIBUTE_SCHEMA": "v0",
    },
    ddtrace_run=True,
    err=None,
)
def test_schema_v0_and_service_override():
    import asyncio

    from tests.contrib.aio_pika.snapshot_app import run_pull_flow

    asyncio.run(run_pull_flow("ddtrace-aio-pika-schema-snapshot"))


@pytest.mark.snapshot(ignores=["meta.tracestate"])
@pytest.mark.subprocess(
    env={
        "DD_AIO_PIKA_DISTRIBUTED_TRACING": "true",
        "DD_AIO_PIKA_SERVICE_NAME": "aio-pika-service-alias",
        "DD_SERVICE": "application-service",
        "DD_TRACE_SPAN_ATTRIBUTE_SCHEMA": "v1",
    },
    ddtrace_run=True,
    err=None,
)
def test_schema_v1_and_service_name_alias():
    import asyncio

    from tests.contrib.aio_pika.snapshot_app import run_pull_flow

    asyncio.run(run_pull_flow("ddtrace-aio-pika-schema-snapshot"))
