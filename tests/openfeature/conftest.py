"""
Shared fixtures for openfeature tests.
"""

import time

from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.openfeature._provider import _provider_instances
from tests.utils import override_global_config


@pytest.fixture(autouse=True)
def fast_initialization_timeout():
    """
    Override the provider initialization timeout to 100ms for all tests.

    The production default is 30s (matching other SDKs), but that makes any
    test that calls api.set_provider() without pre-loaded config hang for 30s.
    Tests that need to verify timeout/blocking behaviour set their own explicit
    initialization_timeout= on DataDogProvider() directly, which takes priority
    over the config value.
    """
    with override_global_config({"initialization_timeout_ms": 100}):
        yield


@pytest.fixture
def wait_for_provider_registration():
    """Wait until the provider's background initialize() has registered for config callbacks."""

    def wait(provider, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if provider in _provider_instances:
                return
            time.sleep(0.01)
        raise AssertionError("provider was not registered for OpenFeature configuration callbacks")

    return wait


@pytest.fixture
def wait_for_provider_ready():
    """Wait until the provider has observed configuration and moved to READY."""

    def wait(provider, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if provider._status == ProviderStatus.READY and provider._config_received.is_set():
                return
            time.sleep(0.01)
        raise AssertionError("provider did not become READY")

    return wait


@pytest.fixture
def wait_for_client_status():
    """Wait until the OpenFeature client reports the expected provider status."""

    def wait(client, status, timeout=1.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if client.get_provider_status() == status:
                return
            time.sleep(0.01)
        raise AssertionError("client did not report provider status %s" % status)

    return wait
