from contextlib import asynccontextmanager

import aio_pika
from aio_pika import DeliveryMode
from aio_pika import ExchangeType
from aio_pika import Message

from tests.contrib.config import RABBITMQ_CONFIG


RABBITMQ_URL = "amqp://{user}:{password}@{host}:{port}/".format(**RABBITMQ_CONFIG)
EXCHANGE_NAME = "test_exchange_aiopika"
QUEUE_NAME = "test_queue_aiopika"
ROUTING_KEY = "test_routing_key"


def make_message(body="test message", headers=None):
    """Helper to create an aio-pika Message with standard defaults."""
    return Message(
        body=body.encode() if isinstance(body, str) else body,
        content_type="text/plain",
        delivery_mode=DeliveryMode.NOT_PERSISTENT,
        headers=headers or {},
    )


@asynccontextmanager
async def aio_pika_ctx(
    exchange_name=EXCHANGE_NAME,
    queue_name=QUEUE_NAME,
    routing_key=ROUTING_KEY,
):
    """Async context manager that sets up a RabbitMQ connection, exchange, and queue.

    Yields (exchange, queue) and handles full teardown on exit.  All async
    lifecycle stays inside the test's own event loop, avoiding pytest-asyncio
    per-test loop teardown issues that arise with async pytest fixtures.
    """
    connection = await aio_pika.connect(RABBITMQ_URL)
    exchange = None
    queue = None
    try:
        channel = await connection.channel()
        await channel.set_qos(prefetch_count=1)

        exchange = await channel.declare_exchange(
            exchange_name,
            type=ExchangeType.DIRECT,
            durable=False,
            auto_delete=True,
        )
        queue = await channel.declare_queue(
            queue_name,
            durable=False,
            auto_delete=True,
        )
        await queue.bind(exchange, routing_key=routing_key)
        await queue.purge()

        yield channel, exchange, queue

    finally:
        if queue is not None and exchange is not None:
            try:
                await queue.unbind(exchange, routing_key=routing_key)
                await queue.delete()
            except Exception:
                pass
        if exchange is not None:
            try:
                await exchange.delete()
            except Exception:
                pass
        await connection.close()
