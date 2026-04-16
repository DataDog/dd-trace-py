import asyncio
from contextlib import asynccontextmanager
import uuid

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
async def aio_pika_ctx():
    """Async context manager that sets up a RabbitMQ connection, exchange, and queue.

    Yields (channel, exchange, queue) and handles teardown on exit. Names are
    randomised per-call so parallel test runs don't share state.
    """
    uid = uuid.uuid4().hex[:8]
    exchange_name = f"{EXCHANGE_NAME}-{uid}"
    queue_name = f"{QUEUE_NAME}-{uid}"
    routing_key = f"{ROUTING_KEY}-{uid}"

    connection = await aio_pika.connect(RABBITMQ_URL)
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

        yield channel, exchange, queue, routing_key

    finally:
        # auto_delete=True on both exchange and queue means the broker cleans
        # them up once all consumers disconnect. Closing the connection is enough.
        await connection.close()


async def queue_get_with_retry(
    queue: aio_pika.abc.AbstractQueue,
    timeout: float = 5.0,
    poll_interval: float = 0.05,
    **kwargs,
) -> aio_pika.abc.AbstractIncomingMessage:
    """Poll queue.get() until a message arrives or *timeout* seconds elapse.

    Avoids a fixed asyncio.sleep() before queue.get(), which is brittle when
    the broker is slow. Uses a short poll_interval so tests stay fast.
    """
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            msg = await queue.get(fail=True, **kwargs)
            return msg
        except aio_pika.exceptions.QueueEmpty:
            pass
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise TimeoutError(f"No message in queue after {timeout}s")
        await asyncio.sleep(min(poll_interval, remaining))
