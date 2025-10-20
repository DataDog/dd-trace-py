import pytest

from tests.utils import override_config

from ddtrace.contrib.internal.aiokafka.patch import patch
from ddtrace.contrib.internal.aiokafka.patch import unpatch

from .utils import BOOTSTRAP_SERVERS
from .utils import KEY
from .utils import PAYLOAD
from .utils import create_topic
from .utils import producer_ctx
from .utils import consumer_ctx


@pytest.fixture(autouse=True)
def patch_aiokafka():
    """Automatically patch aiokafka for all tests in this class"""
    patch()
    yield
    unpatch()

@pytest.mark.asyncio
async def test_data_streams_headers():
    topic = await create_topic("data_streams_headers")

    async with producer_ctx([BOOTSTRAP_SERVERS]) as producer:
        await producer.send_and_wait(topic, value=PAYLOAD)

    async with consumer_ctx([topic]) as consumer:
        result = await consumer.getone()
        await consumer.commit()

    assert 1 == len(result.headers)
    assert "dd-pathway-ctx-base64" == result.headers[0][0]