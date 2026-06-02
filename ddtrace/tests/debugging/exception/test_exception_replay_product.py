from unittest.mock import patch

import pytest

import ddtrace.debugging._products.exception_replay as er_product
from ddtrace.debugging._products.exception_replay import APMCapabilities
from ddtrace.debugging._products.exception_replay import apm_tracing_rc
from ddtrace.debugging._products.exception_replay import enabled
from ddtrace.debugging._products.exception_replay import post_preload
from ddtrace.debugging._products.exception_replay import restart


def test_post_preload_is_noop():
    assert post_preload() is None


def test_restart_is_noop():
    assert restart() is None
    assert restart(join=True) is None


def test_enabled_reflects_config(monkeypatch):
    monkeypatch.setenv("DD_EXCEPTION_REPLAY_ENABLED", "true")
    from ddtrace.debugging._config import er_config as config

    assert enabled() == config.enabled


@pytest.mark.subprocess
def test_start_enables_span_exception_handler():
    from unittest.mock import patch

    # SpanExceptionHandler is imported locally inside start(), patch at the source module
    with patch("ddtrace.debugging._exception.replay.SpanExceptionHandler") as mock_seh:
        from ddtrace.debugging._products.exception_replay import start

        start()
        mock_seh.enable.assert_called_once()


@pytest.mark.subprocess
def test_stop_disables_span_exception_handler():
    from unittest.mock import patch

    with patch("ddtrace.debugging._exception.replay.SpanExceptionHandler") as mock_seh:
        from ddtrace.debugging._products.exception_replay import stop

        stop()
        mock_seh.disable.assert_called_once()

    with patch("ddtrace.debugging._exception.replay.SpanExceptionHandler") as mock_seh:
        from ddtrace.debugging._products.exception_replay import stop

        stop(join=True)
        mock_seh.disable.assert_called_once()


def test_apm_tracing_rc_none_value_is_noop():
    with patch.object(er_product, "start") as mock_start, patch.object(er_product, "stop") as mock_stop:
        apm_tracing_rc({"exception_replay_enabled": None}, None)
        mock_start.assert_not_called()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_missing_key_is_noop():
    with patch.object(er_product, "start") as mock_start, patch.object(er_product, "stop") as mock_stop:
        apm_tracing_rc({}, None)
        mock_start.assert_not_called()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_starts_when_not_env_overridden_and_enabled(monkeypatch):
    from ddtrace.debugging._config import er_config as config

    # Ensure the env var is absent so full_name is not in config.source
    monkeypatch.delenv(config.spec.enabled.full_name, raising=False)

    with patch.object(er_product, "start") as mock_start, patch.object(er_product, "stop") as mock_stop:
        apm_tracing_rc({"exception_replay_enabled": True}, None)
        mock_start.assert_called_once()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_stops_when_not_env_overridden_and_disabled(monkeypatch):
    from ddtrace.debugging._config import er_config as config

    monkeypatch.delenv(config.spec.enabled.full_name, raising=False)

    with patch.object(er_product, "start") as mock_start, patch.object(er_product, "stop") as mock_stop:
        apm_tracing_rc({"exception_replay_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_stops_when_env_enabled_false_and_rc_enabled(monkeypatch):
    """When env sets enabled=False, RC cannot override to re-enable."""
    from ddtrace.debugging._config import er_config as config

    full_name = config.spec.enabled.full_name
    # setenv makes full_name visible in config.source; patch enabled since it is cached at init time
    monkeypatch.setenv(full_name, "false")

    with (
        patch.object(er_product, "start") as mock_start,
        patch.object(er_product, "stop") as mock_stop,
        patch.object(config, "enabled", False),
    ):
        apm_tracing_rc({"exception_replay_enabled": True}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_remote_disable_when_env_enabled(monkeypatch):
    """RC can disable even when env sets enabled=True."""
    from ddtrace.debugging._config import er_config as config

    full_name = config.spec.enabled.full_name
    monkeypatch.setenv(full_name, "true")

    with (
        patch.object(er_product, "start") as mock_start,
        patch.object(er_product, "stop") as mock_stop,
        patch.object(config, "enabled", True),
    ):
        apm_tracing_rc({"exception_replay_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_remote_reenable_when_no_env_override(monkeypatch):
    """RC can re-enable after a previous RC disable when env has no override."""
    from ddtrace.debugging._config import er_config as config

    monkeypatch.delenv(config.spec.enabled.full_name, raising=False)

    with patch.object(er_product, "start") as mock_start, patch.object(er_product, "stop") as mock_stop:
        apm_tracing_rc({"exception_replay_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()

    with patch.object(er_product, "start") as mock_start, patch.object(er_product, "stop") as mock_stop:
        apm_tracing_rc({"exception_replay_enabled": True}, None)
        mock_start.assert_called_once()
        mock_stop.assert_not_called()


def test_apm_capabilities_value():
    assert APMCapabilities.APM_TRACING_ENABLE_EXCEPTION_REPLAY == 1 << 39
