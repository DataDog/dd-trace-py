"""
Shared fixtures for openfeature tests.
"""

from typing import Any

import pytest


def set_openfeature_provider(*args: Any, **kwargs: Any) -> None:
    """Register a provider and wait until initialize() finishes when the SDK allows it.

    OpenFeature Python 0.10 made set_provider() non-blocking. Tests that
    evaluate flags immediately after registration must wait, or they see defaults.

    set_provider_and_wait() also propagates ProviderNotReadyError when
    initialize() times out. Most fixtures register before loading FFE config,
    and the autouse fixture sets that timeout to 0, so the error is expected.
    Swallow it: initialization has still finished.
    """
    from openfeature import api
    from openfeature.exception import ProviderNotReadyError

    wait: Any = getattr(api, "set_provider_and_wait", None)
    register: Any = wait if wait is not None else api.set_provider
    try:
        register(*args, **kwargs)
    except ProviderNotReadyError:
        return


@pytest.fixture(autouse=True)
def _no_initialization_wait(monkeypatch):
    """Stop initialize() from spending its full timeout in tests that never deliver config.

    Most tests construct a provider with no configuration available, so the production
    10s wait would be paid once per provider. Tests that care about the wait itself set
    the timeout explicitly, either through this environment variable or the
    initialization_timeout constructor argument.
    """
    monkeypatch.setenv("DD_EXPERIMENTAL_FLAGGING_PROVIDER_INITIALIZATION_TIMEOUT_MS", "0")
