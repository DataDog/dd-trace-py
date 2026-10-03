"""
Shared fixtures for openfeature tests.
"""

from typing import Any

import pytest


def set_openfeature_provider(*args: Any, **kwargs: Any) -> None:
    """Register a provider and wait until initialize() finishes when the SDK allows it.

    OpenFeature Python 0.10 made ``set_provider()`` non-blocking. Tests that
    evaluate flags immediately after registration must wait, or they see defaults.
    """
    from openfeature import api

    wait: Any = getattr(api, "set_provider_and_wait", None)
    if wait is not None:
        wait(*args, **kwargs)
        return
    api.set_provider(*args, **kwargs)


@pytest.fixture(autouse=True)
def _no_initialization_wait(monkeypatch):
    """Stop initialize() from spending its full timeout in tests that never deliver config.

    Most tests construct a provider with no configuration available, so the production
    10s wait would be paid once per provider. Tests that care about the wait itself set
    the timeout explicitly, either through this environment variable or the
    initialization_timeout constructor argument.
    """
    monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS", "0")
