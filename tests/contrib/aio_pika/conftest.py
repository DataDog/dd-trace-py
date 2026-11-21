import asyncio

import aio_pika
import pytest
import pytest_asyncio

from ddtrace.contrib.internal.aio_pika.patch import patch
from ddtrace.contrib.internal.aio_pika.patch import unpatch
from tests.contrib.config import RABBITMQ_CONFIG
from tests.utils import override_config


AMQP_URL = "amqp://{user}:{password}@{host}:{port}/".format(**RABBITMQ_CONFIG)


@pytest.fixture(autouse=True)
def aio_pika_patched():
    patch()
    yield
    unpatch()


@pytest_asyncio.fixture
async def rabbitmq_connection():
    error = None
    for _ in range(30):
        try:
            connection = await aio_pika.connect(AMQP_URL)
            break
        except OSError as exc:
            error = exc
            await asyncio.sleep(0.25)
    else:
        raise RuntimeError("RabbitMQ did not become ready") from error

    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
def aio_pika_distributed_tracing():
    with override_config("aio_pika", {"distributed_tracing_enabled": True}):
        yield
