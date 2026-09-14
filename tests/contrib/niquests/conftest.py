import pytest

from ddtrace.contrib.internal.niquests.patch import patch
from ddtrace.contrib.internal.niquests.patch import unpatch


HTTPBIN = "http://localhost:8001"


@pytest.fixture
def patched_niquests():
    patch()
    try:
        yield
    finally:
        unpatch()
