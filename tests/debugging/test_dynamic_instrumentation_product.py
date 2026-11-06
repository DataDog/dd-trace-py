from unittest.mock import patch

import pytest

import ddtrace.debugging._products.dynamic_instrumentation as di_product
from ddtrace.debugging._products.dynamic_instrumentation import APMCapabilities
from ddtrace.debugging._products.dynamic_instrumentation import apm_tracing_rc
from ddtrace.debugging._products.dynamic_instrumentation import before_fork
from ddtrace.debugging._products.dynamic_instrumentation import enabled
from ddtrace.debugging._products.dynamic_instrumentation import post_preload
from ddtrace.debugging._products.dynamic_instrumentation import restart
from ddtrace.internal.native import RemoteConfigCapabilities


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
    assert APMCapabilities == (RemoteConfigCapabilities.ApmTracingEnableDynamicInstrumentation,)
    assert int(APMCapabilities[0]) == 38


@pytest.mark.subprocess
def test_rc_enable_instruments_preimported_module():
    import importlib
    import os
    from pathlib import Path
    from unittest.mock import patch

    os.environ.pop("DD_DYNAMIC_INSTRUMENTATION_ENABLED", None)
    import ddtrace.auto  # noqa:F401
    from ddtrace.debugging._debugger import Debugger
    from ddtrace.debugging._import import DebuggerModuleWatchdog
    from ddtrace.debugging._probe.remoteconfig import ProbePollerEvent
    from ddtrace.debugging._products.dynamic_instrumentation import apm_tracing_rc
    from ddtrace.internal.module import ModuleWatchdog
    from ddtrace.internal.utils.inspection import ModuleCodeCollector
    from tests.debugging.mocking import MockProbeStatusLogger
    from tests.debugging.mocking import MockSignalUploader
    from tests.debugging.utils import create_snapshot_line_probe
    import tests.submod.stuff as stuff
    from tests.utils import DummyWriter

    assert Debugger._instance is None
    assert not DebuggerModuleWatchdog.is_installed()
    assert not hasattr(ModuleWatchdog._instance, "_origin_map")
    assert ModuleCodeCollector.get_code_objects(stuff)

    with (
        patch.object(Debugger, "__uploader__", MockSignalUploader),
        patch.object(Debugger, "__logger__", MockProbeStatusLogger),
        patch.object(ddtrace.tracer._span_aggregator, "writer", DummyWriter()),
    ):
        try:
            apm_tracing_rc({"dynamic_instrumentation_enabled": True}, None)
            debugger = Debugger._instance
            assert debugger is not None
            assert DebuggerModuleWatchdog.get_by_origin(Path(stuff.__file__)) is stuff
            probe = create_snapshot_line_probe(probe_id="rc-decorated", source_file=stuff.__file__, line=48, rate=1000)
            debugger._on_configuration(ProbePollerEvent.NEW_PROBES, [probe])
            for _ in range(2):
                instance = stuff.Stuff()
                with ddtrace.tracer.trace("rc-enable"):
                    assert instance.decoratedstuff() is instance
                importlib.reload(stuff)
            snapshots = MockSignalUploader.get_collector().test_queue
            assert [snapshot.probe.probe_id for snapshot in snapshots] == [probe.probe_id, probe.probe_id], snapshots
            debugger._on_configuration(ProbePollerEvent.DELETED_PROBES, [probe])
        finally:
            apm_tracing_rc({"dynamic_instrumentation_enabled": False}, None)
        assert not DebuggerModuleWatchdog.is_installed()
