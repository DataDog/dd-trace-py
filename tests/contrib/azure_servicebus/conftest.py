import os

from azure.core.exceptions import ResourceNotFoundError
from azure.servicebus.management import ServiceBusAdministrationClient
import pytest

from tests.contrib.azure_servicebus.common import MANAGEMENT_CONNECTION_STRING
from tests.contrib.azure_servicebus.common import PARALLEL_QUEUE_COUNT
from tests.contrib.azure_servicebus.common import get_queue_name


@pytest.fixture(scope="session", autouse=True)
def ensure_servicebus_test_queue():
    # AIDEV-NOTE: CI GitLab services use the stock emulator image without a mounted Config.json.
    # queue.1 ships with the default emulator config; parallel jobs use queue.2-4 and must be
    # created via the administration client on the emulator management port (5300).
    queue_name = get_queue_name()
    if not os.environ.get("CI") or queue_name == "queue.1":
        return

    admin_client = ServiceBusAdministrationClient.from_connection_string(MANAGEMENT_CONNECTION_STRING)
    try:
        try:
            admin_client.get_queue(queue_name)
        except ResourceNotFoundError:
            admin_client.create_queue(queue_name)
    finally:
        admin_client.close()


@pytest.fixture(autouse=True)
def servicebus_test_queue_env(monkeypatch):
    monkeypatch.setenv("DD_AZURE_SERVICEBUS_TEST_QUEUE", get_queue_name())


@pytest.fixture(scope="session")
def servicebus_parallel_queue_count():
    return PARALLEL_QUEUE_COUNT


def pytest_configure(config):
    if os.environ.get("CI"):
        queue_name = get_queue_name()
        node_index = os.environ.get("CI_NODE_INDEX", "1")
        print(f"azure_servicebus tests using {queue_name} (CI_NODE_INDEX={node_index})")
