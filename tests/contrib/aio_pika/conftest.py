import pytest

from ddtrace.contrib.internal.aio_pika.patch import patch
from ddtrace.contrib.internal.aio_pika.patch import unpatch


@pytest.fixture
def patch_aio_pika():
    patch()
    yield
    unpatch()
