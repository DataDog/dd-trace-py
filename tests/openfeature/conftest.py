"""
Shared fixtures for openfeature tests.
"""

from typing import Any

import pytest


_SOURCE_SELECTION_ENV = (
    "DD_FEATURE_FLAGS_ENABLED",
    "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE",
    "DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_BASE_URL",
    "DD_EXPERIMENTAL_FLAGGING_PROVIDER_ENABLED",
)


def use_configuration_source(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Point DataDogProvider at a configuration where only the given source settings are provided."""
    from ddtrace.internal.settings.openfeature import OpenFeatureConfig

    # Environment variables take precedence over the source mapping, so remove inherited ones.
    for name in _SOURCE_SELECTION_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("ddtrace.internal.openfeature._provider.ffe_config", OpenFeatureConfig(source=env))


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


@pytest.fixture(autouse=True)
def _reset_flag_selection():
    """Each test starts with no process flag selection claimed and no diagnostic reported."""
    from ddtrace.internal.openfeature._flag_selection import _reset_flag_selection_state

    _reset_flag_selection_state()
    yield
    _reset_flag_selection_state()


@pytest.fixture
def count_metric():
    from unittest import mock

    from ddtrace.internal.openfeature import _flag_selection

    # Replace only this module's reference: other threads record their own counts on the process-wide writer.
    with mock.patch.object(_flag_selection, "telemetry_writer") as telemetry_writer:
        yield telemetry_writer.add_count_metric


@pytest.fixture
def poller(monkeypatch):
    import ddtrace.internal.openfeature._agentless_source as source_mod
    from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource
    from tests.openfeature.flag_selection_helpers import MANAGED_TARGET
    from tests.openfeature.flag_selection_helpers import _FakeConn

    def build(responses, flag_selection_sent=True, apply_configuration=None):
        requests: list = []
        applied: list = []
        monkeypatch.setattr(
            source_mod, "get_connection", lambda url, timeout=None: _FakeConn(lambda _: responses.pop(0), requests)
        )
        source = AgentlessConfigurationSource(
            endpoint="https://ufc-server.ff-cdn.datadoghq.com" + MANAGED_TARGET + "&selected_flag_tags=a",
            apply_configuration=apply_configuration or applied.append,
            flag_selection_sent=flag_selection_sent,
        )
        monkeypatch.setattr(source, "_retry_delay", lambda attempt: 0.0)
        source._applied = applied
        return source

    return build


@pytest.fixture
def cdn(monkeypatch):
    """Serve every agentless poll from a swappable responder and record request targets."""
    import ddtrace.internal.openfeature._agentless_source as source_mod
    from ddtrace.internal.openfeature._config import _set_ffe_config
    from tests.openfeature.flag_selection_helpers import _FakeConn
    from tests.openfeature.flag_selection_helpers import _FakeResponse
    from tests.openfeature.flag_selection_helpers import _ufc_body

    class _Cdn:
        requests: list = []

        def __init__(self):
            self.respond = lambda target: _FakeResponse(200, _ufc_body())

    server = _Cdn()
    monkeypatch.setattr(
        source_mod,
        "get_connection",
        lambda url, timeout=None: _FakeConn(lambda target: server.respond(target), server.requests),
    )
    _set_ffe_config(None)
    yield server
    _set_ffe_config(None)


@pytest.fixture
def api_key():
    from tests.utils import override_global_config

    with override_global_config({"_dd_api_key": "secret", "_dd_site": "datadoghq.com", "env": None}):
        yield
