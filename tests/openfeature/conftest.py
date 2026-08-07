"""
Shared fixtures for openfeature tests.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_initialization_wait(monkeypatch):
    """Stop initialize() from spending its full timeout in tests that never deliver config.

    Most tests construct a provider with no configuration available, so the production
    10s wait would be paid once per provider. Tests that care about the wait itself set
    the timeout explicitly, either through this environment variable or the
    initialization_timeout constructor argument.
    """
    monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS", "0")
