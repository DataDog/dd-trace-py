import time

from kafka.admin import KafkaAdminClient
import pytest

from .utils import BOOTSTRAP_SERVERS


@pytest.fixture(scope="session", autouse=True)
def kafka_ready():
    deadline = time.monotonic() + 30
    last_err = None
    while time.monotonic() < deadline:
        try:
            KafkaAdminClient(bootstrap_servers=BOOTSTRAP_SERVERS).close()
            return
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    raise RuntimeError(f"Kafka at {BOOTSTRAP_SERVERS} not ready after 30s: {last_err}")
