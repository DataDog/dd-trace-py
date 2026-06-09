import asyncio
import time

from aiokafka.admin import AIOKafkaAdminClient
import pytest

from tests.contrib.config import KAFKA_CONFIG


@pytest.fixture(scope="session")
def kafka_ready():
    bootstrap = "{}:{}".format(KAFKA_CONFIG["host"], KAFKA_CONFIG["port"])

    async def _wait():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            admin = AIOKafkaAdminClient(bootstrap_servers=[bootstrap])
            try:
                await admin.start()
                await admin.close()
                return
            except Exception:
                await asyncio.sleep(0.5)
        raise RuntimeError("Kafka at {} not ready after 30s".format(bootstrap))

    asyncio.run(_wait())
