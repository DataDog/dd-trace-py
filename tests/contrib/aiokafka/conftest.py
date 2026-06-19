import asyncio
import time

from aiokafka.admin import AIOKafkaAdminClient
import pytest

from tests.contrib.config import KAFKA_CONFIG


@pytest.fixture(scope="session", autouse=True)
def kafka_ready():
    bootstrap = "{}:{}".format(KAFKA_CONFIG["host"], KAFKA_CONFIG["port"])

    async def _wait():
        deadline = time.monotonic() + 30
        last_err = None
        while time.monotonic() < deadline:
            admin = AIOKafkaAdminClient(bootstrap_servers=[bootstrap])
            try:
                await admin.start()
                return
            except Exception as e:
                last_err = e
                await asyncio.sleep(0.5)
            finally:
                try:
                    await admin.close()
                except Exception:
                    pass
        raise RuntimeError("Kafka at {} not ready after 30s: {}".format(bootstrap, last_err))

    asyncio.run(_wait())
