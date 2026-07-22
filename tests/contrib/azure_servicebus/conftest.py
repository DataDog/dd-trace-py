import pytest

from tests.contrib.azure_servicebus.common import get_queue_name


@pytest.fixture(autouse=True)
def servicebus_test_queue_env(monkeypatch):
    monkeypatch.setenv("DD_AZURE_SERVICEBUS_TEST_QUEUE", get_queue_name())
