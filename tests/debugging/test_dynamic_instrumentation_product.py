from unittest.mock import patch

import pytest

import ddtrace.debugging._products.dynamic_instrumentation as di_product
from ddtrace.debugging._products.dynamic_instrumentation import APMCapabilities
from ddtrace.debugging._products.dynamic_instrumentation import apm_tracing_rc
from ddtrace.debugging._products.dynamic_instrumentation import before_fork
from ddtrace.debugging._products.dynamic_instrumentation import enabled
from ddtrace.debugging._products.dynamic_instrumentation import post_preload
from ddtrace.debugging._products.dynamic_instrumentation import restart


def test_post_preload_is_noop():
    assert post_preload() is None


def test_restart_is_noop():
    assert restart() is None
    assert restart(join=True) is None


def test_before_fork_imports_remoteconfig():
    import sys

    sys.modules.pop("ddtrace.debugging._probe.remoteconfig", None)
    before_fork()
    assert "ddtrace.debugging._probe.remoteconfig" in sys.modules
    # Restore: the module was already imported before this test removed it; re-importing is
    # idempotent, so no explicit teardown is needed.


def test_enabled_reflects_config():
    from ddtrace.internal.settings.dynamic_instrumentation import config

    assert enabled() == config.enabled


@pytest.mark.subprocess
def test_start_enables_dynamic_instrumentation():
    from unittest.mock import patch

    # DynamicInstrumentation is imported locally inside start(), patch at the source module
    with patch("ddtrace.debugging.DynamicInstrumentation") as mock_di:
        from ddtrace.debugging._products.dynamic_instrumentation import start

        start()
        mock_di.enable.assert_called_once()


@pytest.mark.subprocess
def test_stop_disables_dynamic_instrumentation():
    from unittest.mock import patch

    with patch("ddtrace.debugging.DynamicInstrumentation") as mock_di:
        from ddtrace.debugging._products.dynamic_instrumentation import stop

        stop()
        mock_di.disable.assert_called_once_with(join=False)

    with patch("ddtrace.debugging.DynamicInstrumentation") as mock_di:
        from ddtrace.debugging._products.dynamic_instrumentation import stop

        stop(join=True)
        mock_di.disable.assert_called_once_with(join=True)


def test_apm_tracing_rc_none_value_is_noop():
    with patch.object(di_product, "start") as mock_start, patch.object(di_product, "stop") as mock_stop:
        apm_tracing_rc({"dynamic_instrumentation_enabled": None}, None)
        mock_start.assert_not_called()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_missing_key_is_noop():
    with patch.object(di_product, "start") as mock_start, patch.object(di_product, "stop") as mock_stop:
        apm_tracing_rc({}, None)
        mock_start.assert_not_called()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_starts_when_not_env_overridden_and_enabled(monkeypatch):
    from ddtrace.internal.settings.dynamic_instrumentation import config

    # Ensure the env var is absent so full_name is not in config.source
    monkeypatch.delenv(config.spec.enabled.full_name, raising=False)

    with patch.object(di_product, "start") as mock_start, patch.object(di_product, "stop") as mock_stop:
        apm_tracing_rc({"dynamic_instrumentation_enabled": True}, None)
        mock_start.assert_called_once()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_stops_when_not_env_overridden_and_disabled(monkeypatch):
    from ddtrace.internal.settings.dynamic_instrumentation import config

    monkeypatch.delenv(config.spec.enabled.full_name, raising=False)

    with patch.object(di_product, "start") as mock_start, patch.object(di_product, "stop") as mock_stop:
        apm_tracing_rc({"dynamic_instrumentation_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_stops_when_env_enabled_false_and_rc_enabled(monkeypatch):
    """When env sets enabled=False, RC cannot override to re-enable."""
    from ddtrace.internal.settings.dynamic_instrumentation import config

    full_name = config.spec.enabled.full_name
    # setenv makes full_name visible in config.source; patch parsed since it is cached at init time
    monkeypatch.setenv(full_name, "false")

    with (
        patch.object(di_product, "start") as mock_start,
        patch.object(di_product, "stop") as mock_stop,
        patch.object(config.parsed, "enabled", False),
    ):
        apm_tracing_rc({"dynamic_instrumentation_enabled": True}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_remote_disable_when_env_enabled(monkeypatch):
    """RC can disable even when env sets enabled=True."""
    from ddtrace.internal.settings.dynamic_instrumentation import config

    full_name = config.spec.enabled.full_name
    monkeypatch.setenv(full_name, "true")

    with (
        patch.object(di_product, "start") as mock_start,
        patch.object(di_product, "stop") as mock_stop,
        patch.object(config.parsed, "enabled", True),
    ):
        apm_tracing_rc({"dynamic_instrumentation_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_remote_reenable_when_no_env_override(monkeypatch):
    """RC can re-enable after a previous RC disable when env has no override."""
    from ddtrace.internal.settings.dynamic_instrumentation import config

    monkeypatch.delenv(config.spec.enabled.full_name, raising=False)

    with patch.object(di_product, "start") as mock_start, patch.object(di_product, "stop") as mock_stop:
        apm_tracing_rc({"dynamic_instrumentation_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()

    with patch.object(di_product, "start") as mock_start, patch.object(di_product, "stop") as mock_stop:
        apm_tracing_rc({"dynamic_instrumentation_enabled": True}, None)
        mock_start.assert_called_once()
        mock_stop.assert_not_called()


def test_apm_capabilities_value():
    assert APMCapabilities.APM_TRACING_ENABLE_DYNAMIC_INSTRUMENTATION == 1 << 38
