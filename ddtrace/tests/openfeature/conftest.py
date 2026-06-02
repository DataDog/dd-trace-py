"""
Shared fixtures for openfeature tests.
"""

import pytest

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
