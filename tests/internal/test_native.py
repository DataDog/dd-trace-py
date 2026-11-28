import json
from unittest.mock import Mock
from unittest.mock import call

import pytest

from ddtrace.internal import telemetry
from ddtrace.internal.native._native import PyCoreConfigResolver
from ddtrace.internal.settings._config import Config


def test_core_config_resolver_reads_manifest_and_applies_programmatic_values(tmp_path):
    manifest = tmp_path / "supported-configurations.json"
    manifest.write_text(
        json.dumps(
            {
                "version": "2",
                "supportedConfigurations": {
                    "DD_SAMPLE_RATE": [{"type": "decimal", "default": "1", "configurationNames": ["sample_rate"]}]
                },
            }
        ),
        encoding="utf-8",
    )
    request = {
        "version": 1,
        "operation": "initialize",
        "manifest": {"kind": "path", "value": str(manifest)},
        "inputs": {
            "layers": [
                {"origin": "env_var", "entries": [{"name": "DD_SAMPLE_RATE", "value": "0.5"}]},
                {
                    "origin": "code",
                    "entries": [{"name": "sample_rate", "value": {"kind": "number", "value": 0.25}}],
                },
            ]
        },
    }
    result = json.loads(PyCoreConfigResolver().request(json.dumps(request).encode()))
    assert result["status"] == "complete"
    assert result["settings"][0]["resolved"] == {
        "value": {"kind": "number", "value": 0.25},
        "origin": "code",
        "input_name": "sample_rate",
    }


@pytest.mark.parametrize(("dd_service", "expected"), [(None, "otel-service"), ("dd-service", "dd-service")])
def test_core_config_uses_the_otel_service_mapping_when_set(monkeypatch, dd_service, expected):
    monkeypatch.setattr(telemetry, "LOCAL_CONFIG", {})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG", {})
    if dd_service is None:
        monkeypatch.delenv("DD_SERVICE", raising=False)
    else:
        monkeypatch.setenv("DD_SERVICE", dd_service)
    monkeypatch.setenv("OTEL_SERVICE_NAME", "otel-service")

    assert Config().service == expected


@pytest.mark.parametrize("buffer_size", ["1_024", str(1 << 80)])
def test_core_config_loads_one_tracer_startup_snapshot(monkeypatch, buffer_size):
    resolve = Mock(wraps=PyCoreConfigResolver().request)
    report = Mock()
    monkeypatch.setattr(telemetry._native, "PyCoreConfigResolver", lambda: Mock(request=resolve))
    monkeypatch.setattr(telemetry.telemetry_writer, "add_configuration", report)
    monkeypatch.setattr(telemetry, "LOCAL_CONFIG", {"DD_ENV": "local", "DD_TRACE_STARTUP_LOGS": "true"})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG", {"DD_ENV": "fleet", "DD_REMOTECONFIG_POLL_SECONDS": "7.5"})
    monkeypatch.setattr(
        telemetry, "FLEET_CONFIG_IDS", {"DD_ENV": "fleet-id", "DD_REMOTECONFIG_POLL_SECONDS": "poll-id"}
    )
    monkeypatch.setenv("DD_APP_KEY", "startup-test-key")
    monkeypatch.setenv("DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", "6")
    monkeypatch.setenv("DD_ENV", "process")
    monkeypatch.setenv("DD_SERVICE", "service")
    monkeypatch.setenv("DD_VERSION", "version")
    monkeypatch.setenv("DD_TRACE_STARTUP_LOGS", " TRUE")
    monkeypatch.setenv("DD_TRACE_WRITER_BUFFER_SIZE_BYTES", buffer_size)
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)

    config = Config()

    resolve.assert_called_once()
    assert config.env == "fleet"
    assert config.service == "service"
    assert config.version == "version"
    assert config._startup_logs_enabled is False
    assert config._trace_writer_buffer_size == int(buffer_size)
    assert config._remote_config_poll_interval == 7.5
    assert config._dd_app_key == "startup-test-key"
    assert all(entry.args[0] != "DD_APP_KEY" for entry in report.call_args_list)
    assert [entry for entry in report.call_args_list if entry.args[0] == "DD_ENV"] == [
        call("DD_ENV", None, "default"),
        call("DD_ENV", "local", "local_stable_config"),
        call("DD_ENV", "process", "env_var"),
        call("DD_ENV", "fleet", "fleet_stable_config", "fleet-id"),
    ]
    assert [entry for entry in report.call_args_list if entry.args[0] == "DD_TRACE_STARTUP_LOGS"] == [
        call("DD_TRACE_STARTUP_LOGS", True, "default"),
        call("DD_TRACE_STARTUP_LOGS", True, "local_stable_config"),
        call("DD_TRACE_STARTUP_LOGS", False, "env_var"),
    ]
    assert [entry for entry in report.call_args_list if entry.args[0] == "DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS"] == [
        call("DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", 5.0, "default"),
        call("DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", 6.0, "env_var"),
        call("DD_REMOTE_CONFIG_POLL_INTERVAL_SECONDS", 7.5, "fleet_stable_config", "poll-id"),
    ]


def test_core_config_keeps_numeric_conversion_errors_in_source_order(monkeypatch):
    monkeypatch.setattr(telemetry, "LOCAL_CONFIG", {"DD_TRACE_WRITER_BUFFER_SIZE_BYTES": "invalid"})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG", {"DD_TRACE_WRITER_BUFFER_SIZE_BYTES": "1024"})

    with pytest.raises(ValueError, match="invalid literal for int"):
        Config()


def test_core_config_accepts_environment_strings_with_surrogate_escapes(monkeypatch):
    monkeypatch.setenv("DD_ENV", "stage-\udcff")

    assert Config().env == "stage-\udcff"


def test_core_config_uses_the_compiled_manifest_default(monkeypatch):
    manifest = json.loads(telemetry.STARTUP_CONFIG_MANIFEST)
    manifest["supportedConfigurations"]["DD_TRACE_STARTUP_LOGS"][0]["default"] = "false"
    monkeypatch.setattr(telemetry, "STARTUP_CONFIG_MANIFEST", json.dumps(manifest))
    monkeypatch.setattr(telemetry, "LOCAL_CONFIG", {})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG", {})
    monkeypatch.delenv("DD_TRACE_STARTUP_LOGS", raising=False)

    assert Config()._startup_logs_enabled is False


@pytest.mark.parametrize("resolver_available", [False, True])
def test_core_config_uses_host_values_when_the_native_schema_is_unavailable(monkeypatch, resolver_available):
    if resolver_available:
        monkeypatch.setattr(telemetry, "STARTUP_CONFIG_MANIFEST", "invalid")
    else:
        monkeypatch.delattr(telemetry._native, "PyCoreConfigResolver")
    monkeypatch.setattr(telemetry, "LOCAL_CONFIG", {})
    monkeypatch.setattr(telemetry, "FLEET_CONFIG", {})
    monkeypatch.delenv("DD_TRACE_STARTUP_LOGS", raising=False)
    monkeypatch.setenv("DD_TRACE_WRITER_BUFFER_SIZE_BYTES", "1_024")

    config = Config()

    assert config._startup_logs_enabled is True
    assert config._trace_writer_buffer_size == 1024


def test_is_panic_exception():
    from ddtrace.internal.native.exceptions import is_panic_exception

    class PanicException(BaseException):
        pass

    # pyo3_runtime is not normally importable, so PanicException is identified
    # by name rather than by isinstance/issubclass.
    PanicException.__module__ = "pyo3_runtime"

    assert is_panic_exception(PanicException("boom")) is True
    assert is_panic_exception(ValueError("boom")) is False
    assert is_panic_exception(BaseException("boom")) is False


@pytest.mark.subprocess(env={"DD_VERSION": "b"})
def test_get_configuration_from_disk_managed_stable_config_priority():
    """
    Verify the order:
    local stable config < environment variables < managed stable config
    """
    import os
    import tempfile

    # Create managed config
    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="managed_config") as managed_config:
        managed_config.write(
            b"""
config_id: "123"
apm_configuration_default:
  DD_VERSION: "c"
"""
        )
        managed_config.flush()

        # Create local config
        with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
            local_config.write(
                b"""
apm_configuration_default:
  DD_VERSION: "a"
  """
            )
            local_config.flush()
            # Ensure managed and local configs can be discovered via envars
            os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name
            os.environ["_DD_SC_MANAGED_FILE_OVERRIDE"] = managed_config.name
            # Import ddtrace to apply configuration
            from ddtrace import config

            # Ensure managed configuration takes precedence over local config and envars
            assert config.version == "c", f"Expected DD_VERSION to be 'c' but got {config.version}"


@pytest.mark.subprocess(parametrize={"DD_TRACE_DEBUG": ["TRUE", "1"]}, err=None)
def test_get_configuration_debug_logs():
    """
    Verify stable config debug log enablement
    """
    import os
    import sys
    import tempfile

    from tests.utils import call_program

    # Create managed config
    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="managed_config") as managed_config:
        managed_config.write(
            b"""
apm_configuration_default:
  DD_VERSION: "c"
"""
        )
        managed_config.flush()

        env = os.environ.copy()
        env["DD_TRACE_DEBUG"] = "true"
        env["_DD_SC_MANAGED_FILE_OVERRIDE"] = managed_config.name

        _, err, status, _ = call_program(sys.executable, "-c", "import ddtrace", env=env)
        assert status == 0, err
        assert b"Read the following static config: StableConfig" in err
        assert b'ConfigMap([("DD_VERSION", "c")]), tags: {}, rules: [] }' in err
        assert b"configurator: Configurator { debug_logs: true }" in err


@pytest.mark.subprocess(parametrize={"DD_VERSION": ["b", None]})
def test_get_configuration_from_disk_local_config_priority(tmp_path):
    """
    Verify the order:
    local stable config < environment variables
    """
    import os
    import tempfile

    # Create local config
    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
apm_configuration_default:
  DD_VERSION: "a"
"""
        )
        local_config.flush()
        # Ensure managed and local configs can be discovered via envars
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name
        # Import ddtrace to apply configuration
        from ddtrace import config

        # Ensure environment variables takes precedence over local config and envars
        if "DD_VERSION" in os.environ:
            assert config.version == "b", f"Expected DD_VERSION to be 'b' but got {config.version}"
        else:
            assert config.version == "a", f"Expected DD_VERSION to be 'a' but got {config.version}"


@pytest.mark.subprocess()
def test_get_configuration_from_disk__host_selector(tmp_path):
    """
    Verify local configurations can be read from a file
    """
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
apm_configuration_default:
  DD_RUNTIME_METRICS_ENABLED: true
"""
        )
        local_config.flush()
        # Provide the local config via an environment variable
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name
        # Ensure runtime metrics is enabled (default value is False)
        from ddtrace import config

        config._runtime_metrics_enabled = True


@pytest.mark.subprocess()
def test_get_configuration_from_disk__service_selector_match():
    # First test -- config matches & should be returned
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
rules:
  - selectors:
    - origin: language
      matches:
        - python
      operator: equals
    configuration:
      DD_VERSION: my-version
"""
        )
        local_config.flush()
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name

        from ddtrace import config

        assert config.version == "my-version", f"Expected DD_VERSION to be 'my-version' but got {config.version}"


@pytest.mark.subprocess()
def test_get_configuration_from_disk__service_selector_not_matched():
    # Second test -- config does not match & should not be returned
    import os
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".yaml", prefix="local_config") as local_config:
        local_config.write(
            b"""
rules:
  - selectors:
    - origin: language
      matches:
        - nodejs
      operator: equals
    configuration:
      DD_VERSION: my-version
"""
        )
        local_config.flush()
        os.environ["_DD_SC_LOCAL_FILE_OVERRIDE"] = local_config.name

        from ddtrace import config

        assert config.version != "my-version", f"Expected DD_VERSION to be 'my-version' but got {config.version}"
