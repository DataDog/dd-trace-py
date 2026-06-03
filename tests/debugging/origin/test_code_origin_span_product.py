from unittest.mock import patch

import pytest

import ddtrace.debugging._products.code_origin.span as co_product
from ddtrace.debugging._products.code_origin.span import APMCapabilities
from ddtrace.debugging._products.code_origin.span import apm_tracing_rc
from ddtrace.debugging._products.code_origin.span import enabled
from ddtrace.debugging._products.code_origin.span import post_preload
from ddtrace.debugging._products.code_origin.span import restart


def test_post_preload_is_noop():
    assert post_preload() is None


def test_restart_is_noop():
    assert restart() is None
    assert restart(join=True) is None


@pytest.mark.subprocess
def test_start_enables_processor():
    from unittest.mock import patch

    with patch("ddtrace.debugging._origin.span.SpanCodeOriginProcessorEntry") as mock_proc:
        from ddtrace.debugging._products.code_origin.span import start

        start()
        mock_proc.enable.assert_called_once()


@pytest.mark.subprocess
def test_stop_disables_processor():
    from unittest.mock import patch

    with patch("ddtrace.debugging._origin.span.SpanCodeOriginProcessorEntry") as mock_proc:
        from ddtrace.debugging._products.code_origin.span import stop

        stop()
        mock_proc.disable.assert_called_once()


@pytest.mark.subprocess
def test_core_event_handler_service_entrypoint_instruments_view():
    """Module-level listener for service_entrypoint.patch calls instrument_view."""
    from unittest.mock import MagicMock
    from unittest.mock import patch

    import ddtrace.debugging._products.code_origin.span  # noqa: F401 — registers the listener
    from ddtrace.internal import core

    f = MagicMock()
    with patch("ddtrace.debugging._origin.span.SpanCodeOriginProcessorEntry") as mock_proc:
        core.dispatch("service_entrypoint.patch", (f,))
        mock_proc.instrument_view.assert_called_with(f)


@pytest.mark.subprocess
def test_core_event_handler_tracer_wrap_instruments_view():
    """Module-level listener for tracer.wrap calls instrument_view."""
    from unittest.mock import MagicMock
    from unittest.mock import patch

    import ddtrace.debugging._products.code_origin.span  # noqa: F401 — registers the listener
    from ddtrace.internal import core

    f = MagicMock()
    with patch("ddtrace.debugging._origin.span.SpanCodeOriginProcessorEntry") as mock_proc:
        core.dispatch("tracer.wrap", (f,))
        mock_proc.instrument_view.assert_called_with(f)


def test_enabled_span_disabled_by_default():
    """enabled() returns False when config.span.enabled is False (the default)."""
    from ddtrace.internal.settings.code_origin import config

    with patch.object(config.span, "enabled", False):
        assert enabled() is False


def test_enabled_span_explicitly_enabled():
    from ddtrace.internal.settings.code_origin import config

    with patch.object(config.span, "enabled", True):
        assert enabled() is True


def test_enabled_dynamic_instrumentation_does_not_enable_code_origin_for_spans():
    from ddtrace.internal.settings.code_origin import config

    with patch.object(config.span, "enabled", False):
        assert enabled() is False


def test_apm_tracing_rc_none_value_is_noop():
    with patch.object(co_product, "start") as mock_start, patch.object(co_product, "stop") as mock_stop:
        apm_tracing_rc({"code_origin_enabled": None}, None)
        mock_start.assert_not_called()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_missing_key_is_noop():
    with patch.object(co_product, "start") as mock_start, patch.object(co_product, "stop") as mock_stop:
        apm_tracing_rc({}, None)
        mock_start.assert_not_called()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_starts_when_not_env_overridden_and_enabled(monkeypatch):
    from ddtrace.internal.settings.code_origin import config

    monkeypatch.delenv(config.span.spec.enabled.full_name, raising=False)

    with patch.object(co_product, "start") as mock_start, patch.object(co_product, "stop") as mock_stop:
        apm_tracing_rc({"code_origin_enabled": True}, None)
        mock_start.assert_called_once()
        mock_stop.assert_not_called()


def test_apm_tracing_rc_stops_when_not_env_overridden_and_disabled(monkeypatch):
    from ddtrace.internal.settings.code_origin import config

    monkeypatch.delenv(config.span.spec.enabled.full_name, raising=False)

    with patch.object(co_product, "start") as mock_start, patch.object(co_product, "stop") as mock_stop:
        apm_tracing_rc({"code_origin_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_stops_when_env_enabled_false_and_rc_enabled(monkeypatch):
    """When env sets span.enabled=False, RC cannot override to re-enable."""
    from ddtrace.internal.settings.code_origin import config

    full_name = config.span.spec.enabled.full_name
    monkeypatch.setenv(full_name, "false")

    with (
        patch.object(co_product, "start") as mock_start,
        patch.object(co_product, "stop") as mock_stop,
        patch.object(config.span, "enabled", False),
    ):
        apm_tracing_rc({"code_origin_enabled": True}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_remote_disable_when_env_enabled(monkeypatch):
    """RC can disable even when env sets span.enabled=True."""
    from ddtrace.internal.settings.code_origin import config

    full_name = config.span.spec.enabled.full_name
    monkeypatch.setenv(full_name, "true")

    with (
        patch.object(co_product, "start") as mock_start,
        patch.object(co_product, "stop") as mock_stop,
        patch.object(config.span, "enabled", True),
    ):
        apm_tracing_rc({"code_origin_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()


def test_apm_tracing_rc_remote_reenable_when_no_env_override(monkeypatch):
    """RC can re-enable after a previous RC disable when env has no override."""
    from ddtrace.internal.settings.code_origin import config

    monkeypatch.delenv(config.span.spec.enabled.full_name, raising=False)

    with patch.object(co_product, "start") as mock_start, patch.object(co_product, "stop") as mock_stop:
        apm_tracing_rc({"code_origin_enabled": False}, None)
        mock_start.assert_not_called()
        mock_stop.assert_called_once()

    with patch.object(co_product, "start") as mock_start, patch.object(co_product, "stop") as mock_stop:
        apm_tracing_rc({"code_origin_enabled": True}, None)
        mock_start.assert_called_once()
        mock_stop.assert_not_called()


def test_apm_capabilities_value():
    assert APMCapabilities.APM_TRACING_ENABLE_CODE_ORIGIN == 1 << 40
